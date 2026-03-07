using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

var app = new ForecastMapApp();
await app.RunAsync(args);

internal sealed class ForecastMapApp
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private static readonly RegionConfig Region = new(
        MinLongitude: 116.0,
        MaxLongitude: 156.0,
        MinLatitude: 20.0,
        MaxLatitude: 50.0,
        LongitudeStep: 3.0,
        LatitudeStep: 3.0);

    private static readonly ModelConfig[] Models =
    [
        new("ECMWF", "ecmwf", "https://api.open-meteo.com/v1/ecmwf"),
        new("GFS", "gfs", "https://api.open-meteo.com/v1/gfs"),
        new("ICON", "icon", "https://api.open-meteo.com/v1/dwd-icon")
    ];

    private readonly HttpClient _http = new()
    {
        Timeout = TimeSpan.FromSeconds(120)
    };

    public async Task RunAsync(string[] args)
    {
        var rootDir = ResolveRootDirectory();
        if (args.Length > 0 && string.Equals(args[0], "--redraw-land-only", StringComparison.OrdinalIgnoreCase))
        {
            if (args.Length < 3)
            {
                throw new ArgumentException("Usage: --redraw-land-only <inputImagePath> <outputImagePath>");
            }

            RedrawLandOverlay(rootDir, args[1], args[2]);
            return;
        }

        var docsDir = Path.Combine(rootDir, "docs");
        var dataDir = Path.Combine(docsDir, "data");
        var stagingDir = Path.Combine(docsDir, "data-next");
        var imageDir = Path.Combine(stagingDir, "images");
        var landFeatures = LoadLandFeatures(rootDir);

        Directory.CreateDirectory(docsDir);
        RecreateDirectory(stagingDir);
        Directory.CreateDirectory(imageDir);

        var generatedAtUtc = DateTimeOffset.UtcNow;
        var generatedAtJst = TimeZoneInfo.ConvertTimeBySystemTimeZoneId(generatedAtUtc, "Tokyo Standard Time");
        var coordinates = Region.BuildCoordinates();

        Console.WriteLine($"Coordinates: {coordinates.Count}");
        Console.WriteLine("Fetching model grids...");

        var allModelForecasts = new Dictionary<string, ModelForecast>(StringComparer.OrdinalIgnoreCase);
        foreach (var model in Models)
        {
            Console.WriteLine($"  - {model.DisplayName}");
            allModelForecasts[model.Key] = await FetchModelForecastAsync(model, coordinates, generatedAtUtc);
            await Task.Delay(3000);
        }

        var sharedSlots = BuildSharedSlots(allModelForecasts.Values.ToArray(), generatedAtJst);
        if (sharedSlots.Count == 0)
        {
            throw new InvalidOperationException("No common forecast slots were available across ECMWF, GFS, and ICON.");
        }

        Console.WriteLine($"Rendering {sharedSlots.Count} slots...");

        var manifestSlots = new List<ManifestSlot>();
        foreach (var slot in sharedSlots)
        {
            var slotModels = new List<ManifestSlotModel>();
            foreach (var model in Models)
            {
                var forecast = allModelForecasts[model.Key];
                var mapData = forecast.BuildGridForSlot(slot.ForecastTimeJst);
                var modelDir = Path.Combine(imageDir, model.Key);
                Directory.CreateDirectory(modelDir);

                var fileName = $"{slot.Id}.jpg";
                var absoluteImagePath = Path.Combine(modelDir, fileName);
                var relativeImagePath = $"./data/images/{model.Key}/{fileName}".Replace('\\', '/');

                RenderForecastImage(
                    absoluteImagePath,
                    model,
                    slot.ForecastTimeJst,
                    generatedAtJst,
                    forecast.ModelRunUtc,
                    mapData,
                    landFeatures);

                slotModels.Add(new ManifestSlotModel(
                    model.Key,
                    model.DisplayName,
                    relativeImagePath,
                    slot.ForecastTimeJst.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture),
                    forecast.ModelRunUtc.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture)));
            }

            manifestSlots.Add(new ManifestSlot(
                slot.Id,
                slot.Label,
                slot.ForecastTimeJst.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture),
                slotModels));
        }

        var manifest = new Manifest(
            "Japan Surrounding Forecast Viewer",
            generatedAtJst.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture),
            "Asia/Tokyo",
            "Open-Meteo model APIs (ECMWF / GFS / ICON)",
            "Model run time is estimated from the latest available cycle when the forecast endpoint does not expose it directly.",
            manifestSlots);

        var manifestJson = JsonSerializer.Serialize(manifest, JsonOptions);
        await File.WriteAllTextAsync(Path.Combine(stagingDir, "manifest.json"), manifestJson, Encoding.UTF8);
        await File.WriteAllTextAsync(Path.Combine(stagingDir, "manifest.js"), $"window.TENKI_MANIFEST = {manifestJson};", Encoding.UTF8);

        if (Directory.Exists(dataDir))
        {
            Directory.Delete(dataDir, recursive: true);
        }

        Directory.Move(stagingDir, dataDir);

        Console.WriteLine("Done.");
        Console.WriteLine($"Generated data: {dataDir}");
        Console.WriteLine($"Open viewer: {Path.Combine(docsDir, "index.html")}");
    }

    private async Task<ModelForecast> FetchModelForecastAsync(ModelConfig model, IReadOnlyList<Coordinate> coordinates, DateTimeOffset generatedAtUtc)
    {
        var locationForecasts = new List<LocationForecast>(coordinates.Count);
        const int batchSize = 20;

        for (var offset = 0; offset < coordinates.Count; offset += batchSize)
        {
            var batch = coordinates.Skip(offset).Take(batchSize).ToArray();
            var response = await FetchBatchAsync(model, batch);
            locationForecasts.AddRange(response);
            await Task.Delay(2500);
        }

        if (locationForecasts.Count != coordinates.Count)
        {
            throw new InvalidOperationException($"Expected {coordinates.Count} locations for {model.DisplayName}, but received {locationForecasts.Count}.");
        }

        var sampleTimes = locationForecasts[0].HourlyTimes;
        var modelRunUtc = EstimateModelRunTime(generatedAtUtc);
        return new ModelForecast(model, locationForecasts, sampleTimes, modelRunUtc);
    }

    private async Task<List<LocationForecast>> FetchBatchAsync(ModelConfig model, Coordinate[] batch)
    {
        var latitudes = string.Join(',', batch.Select(point => point.Latitude.ToString("0.##", CultureInfo.InvariantCulture)));
        var longitudes = string.Join(',', batch.Select(point => point.Longitude.ToString("0.##", CultureInfo.InvariantCulture)));
        var url =
            $"{model.Endpoint}?latitude={latitudes}&longitude={longitudes}" +
            "&hourly=pressure_msl" +
            "&forecast_hours=169" +
            "&timezone=Asia%2FTokyo" +
            "&format=json";

        using var response = await SendWithRetryAsync(url);
        await using var stream = await response.Content.ReadAsStreamAsync();
        using var document = await JsonDocument.ParseAsync(stream);

        if (document.RootElement.ValueKind == JsonValueKind.Object)
        {
            return [ParseLocationForecast(document.RootElement, batch[0])];
        }

        var result = new List<LocationForecast>(document.RootElement.GetArrayLength());
        var batchIndex = 0;
        foreach (var item in document.RootElement.EnumerateArray())
        {
            result.Add(ParseLocationForecast(item, batch[batchIndex++]));
        }

        return result;
    }

    private static LocationForecast ParseLocationForecast(JsonElement element, Coordinate requestedCoordinate)
    {
        var hourly = element.GetProperty("hourly");

        var times = hourly.GetProperty("time").EnumerateArray()
            .Select(item =>
            {
                var local = DateTime.ParseExact(
                    item.GetString()!,
                    "yyyy-MM-dd'T'HH:mm",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.None);
                return new DateTimeOffset(local, TimeSpan.FromHours(9));
            })
            .ToArray();

        return new LocationForecast(
            requestedCoordinate.Latitude,
            requestedCoordinate.Longitude,
            times,
            ParseDoubleArray(hourly.GetProperty("pressure_msl")));
    }

    private static double[] ParseDoubleArray(JsonElement element)
    {
        var result = new double[element.GetArrayLength()];
        var index = 0;
        foreach (var item in element.EnumerateArray())
        {
            result[index++] = item.ValueKind == JsonValueKind.Number ? item.GetDouble() : double.NaN;
        }

        return result;
    }

    private static List<ForecastSlot> BuildSharedSlots(IReadOnlyList<ModelForecast> forecasts, DateTimeOffset generatedAtJst)
    {
        var commonTimes = forecasts[0].HourlyTimes.ToHashSet();
        foreach (var forecast in forecasts.Skip(1))
        {
            commonTimes.IntersectWith(forecast.HourlyTimes);
        }

        var roundedStart = RoundUpToHour(generatedAtJst);
        var maxForecast = roundedStart.AddHours(168);
        var slots = new List<ForecastSlot>();

        foreach (var time in commonTimes.OrderBy(value => value))
        {
            if (time < roundedStart || time > maxForecast)
            {
                continue;
            }

            var leadHours = (time - roundedStart).TotalHours;
            if (leadHours < 0)
            {
                continue;
            }

            var include = leadHours <= 72 ? time.Hour % 3 == 0 : time.Hour == 0;
            if (!include)
            {
                continue;
            }

            slots.Add(new ForecastSlot(
                time.ToString("yyyyMMdd'T'HHmm", CultureInfo.InvariantCulture),
                $"{time:MM/dd HH:mm} JST",
                time));
        }

        return slots;
    }

    private static void RenderForecastImage(
        string outputPath,
        ModelConfig model,
        DateTimeOffset forecastTimeJst,
        DateTimeOffset generatedAtJst,
        DateTimeOffset modelRunUtc,
        GridSnapshot snapshot,
        IReadOnlyList<LandFeature> landFeatures)
    {
        const int width = 1280;
        const int height = 960;
        const int marginLeft = 96;
        const int marginRight = 48;
        const int marginTop = 92;
        const int marginBottom = 76;

        var plot = new RectangleF(
            marginLeft,
            marginTop,
            width - marginLeft - marginRight,
            height - marginTop - marginBottom);

        using var bitmap = new Bitmap(width, height);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
        graphics.Clear(Color.WhiteSmoke);

        using var backgroundBrush = new LinearGradientBrush(
            new PointF(0, 0),
            new PointF(width, height),
            Color.FromArgb(250, 252, 248),
            Color.FromArgb(231, 239, 244));
        graphics.FillRectangle(backgroundBrush, 0, 0, width, height);

        using var oceanBrush = new SolidBrush(Color.FromArgb(214, 228, 237));
        graphics.FillRectangle(oceanBrush, plot);

        DrawGridLines(graphics, plot);
        DrawLandFeatures(graphics, plot, landFeatures);
        DrawContours(graphics, plot, snapshot);
        DrawFrame(graphics, plot);
        DrawHeader(graphics, width, model, forecastTimeJst, generatedAtJst, modelRunUtc);

        SaveJpeg(bitmap, outputPath, 92L);
    }

    private static void DrawGridLines(Graphics graphics, RectangleF plot)
    {
        using var linePen = new Pen(Color.FromArgb(90, 120, 138, 153), 1f);
        using var textBrush = new SolidBrush(Color.FromArgb(130, 69, 84, 96));
        using var font = new Font("Yu Gothic UI", 10f, FontStyle.Regular);

        for (var lon = 120; lon <= 155; lon += 5)
        {
            var x = Lerp(plot.Left, plot.Right, (float)((lon - Region.MinLongitude) / (Region.MaxLongitude - Region.MinLongitude)));
            graphics.DrawLine(linePen, x, plot.Top, x, plot.Bottom);
            graphics.DrawString($"{lon}E", font, textBrush, x + 4, plot.Bottom + 6);
        }

        for (var lat = 20; lat <= 50; lat += 5)
        {
            var y = Lerp(plot.Bottom, plot.Top, (float)((lat - Region.MinLatitude) / (Region.MaxLatitude - Region.MinLatitude)));
            graphics.DrawLine(linePen, plot.Left, y, plot.Right, y);
            graphics.DrawString($"{lat}N", font, textBrush, plot.Left - 46, y - 7);
        }
    }

    private static void DrawContours(Graphics graphics, RectangleF plot, GridSnapshot snapshot)
    {
        var minPressure = Math.Floor(snapshot.MinPressure / 4.0) * 4.0;
        var maxPressure = Math.Ceiling(snapshot.MaxPressure / 4.0) * 4.0;

        for (var level = minPressure; level <= maxPressure; level += 4.0)
        {
            var strongLine = Math.Abs(level % 8.0) < 0.001;
            using var pen = new Pen(
                strongLine ? Color.FromArgb(230, 34, 42, 47) : Color.FromArgb(175, 70, 78, 84),
                strongLine ? 2.4f : 1.5f);
            DrawContourLevel(graphics, plot, snapshot, level, pen, strongLine);
        }
    }

    private static void DrawContourLevel(Graphics graphics, RectangleF plot, GridSnapshot snapshot, double level, Pen pen, bool drawLabels)
    {
        var rows = snapshot.Latitudes.Count;
        var cols = snapshot.Longitudes.Count;
        var labelsDrawn = 0;

        using var font = new Font("Georgia", 10f, FontStyle.Bold);
        using var labelBrush = new SolidBrush(Color.FromArgb(225, 22, 28, 34));
        using var haloBrush = new SolidBrush(Color.FromArgb(190, 255, 255, 255));

        for (var row = 0; row < rows - 1; row++)
        {
            for (var col = 0; col < cols - 1; col++)
            {
                var v00 = snapshot.Pressure[row, col];
                var v10 = snapshot.Pressure[row, col + 1];
                var v11 = snapshot.Pressure[row + 1, col + 1];
                var v01 = snapshot.Pressure[row + 1, col];

                if (double.IsNaN(v00) || double.IsNaN(v10) || double.IsNaN(v11) || double.IsNaN(v01))
                {
                    continue;
                }

                var points = ContourCell(plot, row, col, rows, cols, level, v00, v10, v11, v01);
                for (var index = 0; index + 1 < points.Count; index += 2)
                {
                    graphics.DrawLine(pen, points[index], points[index + 1]);

                    if (!drawLabels || labelsDrawn > 10)
                    {
                        continue;
                    }

                    var p0 = points[index];
                    var p1 = points[index + 1];
                    var mid = new PointF((p0.X + p1.X) / 2f, (p0.Y + p1.Y) / 2f);
                    var label = level.ToString("0", CultureInfo.InvariantCulture);
                    var size = graphics.MeasureString(label, font);
                    graphics.FillRectangle(haloBrush, mid.X - size.Width / 2f - 2f, mid.Y - size.Height / 2f, size.Width + 4f, size.Height);
                    graphics.DrawString(label, font, labelBrush, mid.X - size.Width / 2f, mid.Y - size.Height / 2f);
                    labelsDrawn++;
                }
            }
        }
    }

    private static List<PointF> ContourCell(RectangleF plot, int row, int col, int rows, int cols, double level, double v00, double v10, double v11, double v01)
    {
        var topLeft = GridPoint(plot, row, col, rows, cols);
        var topRight = GridPoint(plot, row, col + 1, rows, cols);
        var bottomRight = GridPoint(plot, row + 1, col + 1, rows, cols);
        var bottomLeft = GridPoint(plot, row + 1, col, rows, cols);
        var hits = new List<PointF>(4);

        MaybeAddIntersection(hits, level, v00, v10, topLeft, topRight);
        MaybeAddIntersection(hits, level, v10, v11, topRight, bottomRight);
        MaybeAddIntersection(hits, level, v11, v01, bottomRight, bottomLeft);
        MaybeAddIntersection(hits, level, v01, v00, bottomLeft, topLeft);

        if (hits.Count == 4)
        {
            var average = (v00 + v10 + v11 + v01) / 4.0;
            return average >= level
                ? [hits[0], hits[1], hits[2], hits[3]]
                : [hits[0], hits[3], hits[1], hits[2]];
        }

        return hits;
    }

    private static void MaybeAddIntersection(List<PointF> hits, double level, double a, double b, PointF p0, PointF p1)
    {
        var crosses = (a <= level && b > level) || (a >= level && b < level);
        if (!crosses || Math.Abs(a - b) < 0.0001)
        {
            return;
        }

        var ratio = (float)((level - a) / (b - a));
        hits.Add(new PointF(p0.X + (p1.X - p0.X) * ratio, p0.Y + (p1.Y - p0.Y) * ratio));
    }

    private static void DrawLandFeatures(Graphics graphics, RectangleF plot, IReadOnlyList<LandFeature> landFeatures)
    {
        using var landBrush = new SolidBrush(Color.FromArgb(224, 233, 229, 212));
        using var coastOutlinePen = new Pen(Color.FromArgb(235, 58, 65, 72), 2.2f)
        {
            LineJoin = LineJoin.Round
        };
        using var coastInnerPen = new Pen(Color.FromArgb(180, 255, 255, 255), 0.9f)
        {
            LineJoin = LineJoin.Round
        };
        using var labelBrush = new SolidBrush(Color.FromArgb(180, 41, 52, 58));
        using var japanBrush = new SolidBrush(Color.FromArgb(220, 47, 57, 63));
        using var seaFont = new Font("Georgia", 11f, FontStyle.Italic);
        using var japanFont = new Font("Yu Gothic UI", 16f, FontStyle.Bold);

        foreach (var feature in landFeatures)
        {
            using var path = new GraphicsPath(FillMode.Alternate);
            foreach (var polygon in feature.Polygons)
            {
                foreach (var ring in polygon.Rings)
                {
                    if (ring.Count < 3)
                    {
                        continue;
                    }

                    var points = ring.Select(point => Project(plot, point.Longitude, point.Latitude)).ToArray();
                    path.AddPolygon(points);
                }
            }

            graphics.FillPath(landBrush, path);
            graphics.DrawPath(coastOutlinePen, path);
            graphics.DrawPath(coastInnerPen, path);
        }

        graphics.DrawString("Japan", japanFont, japanBrush, Project(plot, 137.5, 37.5));
        graphics.DrawString("Sea of Japan", seaFont, labelBrush, Project(plot, 130.6, 39.1));
        graphics.DrawString("Pacific Ocean", seaFont, labelBrush, Project(plot, 144.3, 28.3));
        graphics.DrawString("East China Sea", seaFont, labelBrush, Project(plot, 124.6, 27.9));
    }

    private static void DrawFrame(Graphics graphics, RectangleF plot)
    {
        using var borderPen = new Pen(Color.FromArgb(180, 52, 67, 78), 2f);
        graphics.DrawRectangle(borderPen, plot.X, plot.Y, plot.Width, plot.Height);

        using var legendBg = new SolidBrush(Color.FromArgb(210, 255, 255, 255));
        using var legendText = new SolidBrush(Color.FromArgb(220, 31, 44, 54));
        using var legendFont = new Font("Yu Gothic UI", 10f, FontStyle.Regular);

        var legendRect = new RectangleF(plot.Right - 206, plot.Top + 14, 192, 84);
        graphics.FillRectangle(legendBg, legendRect);
        graphics.DrawRectangle(Pens.Gray, legendRect.X, legendRect.Y, legendRect.Width, legendRect.Height);
        graphics.DrawString("Surface Pressure", legendFont, legendText, legendRect.X + 10, legendRect.Y + 8);
        graphics.DrawString("Contour interval: 4 hPa", legendFont, legendText, legendRect.X + 10, legendRect.Y + 31);
        graphics.DrawString("Bold line: every 8 hPa", legendFont, legendText, legendRect.X + 10, legendRect.Y + 53);
    }

    private static void DrawHeader(Graphics graphics, int width, ModelConfig model, DateTimeOffset forecastTimeJst, DateTimeOffset generatedAtJst, DateTimeOffset modelRunUtc)
    {
        using var titleBg = new SolidBrush(Color.FromArgb(224, 246, 248, 244));
        graphics.FillRectangle(titleBg, 24, 22, width - 48, 52);

        using var titleBrush = new SolidBrush(Color.FromArgb(230, 28, 35, 39));
        using var metaBrush = new SolidBrush(Color.FromArgb(210, 50, 63, 71));
        using var titleFont = new Font("Georgia", 20f, FontStyle.Bold);
        using var metaFont = new Font("Yu Gothic UI", 11f, FontStyle.Regular);

        graphics.DrawString($"{model.DisplayName}   Surface Pressure", titleFont, titleBrush, 38, 28);
        var metaText =
            $"Forecast: {forecastTimeJst:yyyy-MM-dd HH:mm} JST    " +
            $"Model run: {modelRunUtc:yyyy-MM-dd HH:mm} UTC    " +
            $"Created: {generatedAtJst:yyyy-MM-dd HH:mm} JST";
        graphics.DrawString(metaText, metaFont, metaBrush, 40, 58);
    }

    private static void SaveJpeg(Bitmap bitmap, string outputPath, long quality)
    {
        var encoder = ImageCodecInfo.GetImageDecoders().First(codec => codec.FormatID == ImageFormat.Jpeg.Guid);
        using var parameters = new EncoderParameters(1);
        parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, quality);
        bitmap.Save(outputPath, encoder, parameters);
    }

    private static PointF GridPoint(RectangleF plot, int row, int col, int rows, int cols)
    {
        var x = plot.Left + plot.Width * (col / (float)(cols - 1));
        var y = plot.Top + plot.Height * (row / (float)(rows - 1));
        return new PointF(x, y);
    }

    private static PointF Project(RectangleF plot, double longitude, double latitude)
    {
        var xRatio = (float)((longitude - Region.MinLongitude) / (Region.MaxLongitude - Region.MinLongitude));
        var yRatio = (float)((Region.MaxLatitude - latitude) / (Region.MaxLatitude - Region.MinLatitude));
        return new PointF(plot.Left + plot.Width * xRatio, plot.Top + plot.Height * yRatio);
    }

    private static float Lerp(float start, float end, float amount) => start + (end - start) * amount;

    private static void RecreateDirectory(string directoryPath)
    {
        if (Directory.Exists(directoryPath))
        {
            Directory.Delete(directoryPath, recursive: true);
        }

        Directory.CreateDirectory(directoryPath);
    }

    private static string ResolveRootDirectory()
    {
        var current = AppContext.BaseDirectory;
        var directory = new DirectoryInfo(current);
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "やること.txt")))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        throw new DirectoryNotFoundException("Could not locate the repository root.");
    }

    private static DateTimeOffset EstimateModelRunTime(DateTimeOffset generatedAtUtc)
    {
        var reference = generatedAtUtc.AddHours(-2);
        var cycleHour = (reference.Hour / 6) * 6;
        return new DateTimeOffset(reference.Year, reference.Month, reference.Day, cycleHour, 0, 0, TimeSpan.Zero);
    }

    private static DateTimeOffset RoundUpToHour(DateTimeOffset time)
    {
        if (time.Minute == 0 && time.Second == 0)
        {
            return time;
        }

        return new DateTimeOffset(time.Year, time.Month, time.Day, time.Hour, 0, 0, time.Offset).AddHours(1);
    }

    private static void RedrawLandOverlay(string rootDir, string inputImagePath, string outputImagePath)
    {
        var landFeatures = LoadLandFeatures(rootDir);
        var inputPath = Path.GetFullPath(Path.Combine(rootDir, inputImagePath));
        var outputPath = Path.GetFullPath(Path.Combine(rootDir, outputImagePath));

        using var bitmap = new Bitmap(inputPath);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;

        var plot = new RectangleF(96, 92, bitmap.Width - 96 - 48, bitmap.Height - 92 - 76);
        DrawLandFeatures(graphics, plot, landFeatures);

        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
        SaveJpeg(bitmap, outputPath, 95L);
        Console.WriteLine($"Saved land-overlay preview: {outputPath}");
    }

    private static List<LandFeature> LoadLandFeatures(string rootDir)
    {
        var dataPath = Path.Combine(rootDir, "tools", "ForecastMapGenerator", "map-data", "japan-region-land.geojson");
        using var stream = File.OpenRead(dataPath);
        using var document = JsonDocument.Parse(stream);

        var features = new List<LandFeature>();
        foreach (var feature in document.RootElement.GetProperty("features").EnumerateArray())
        {
            var properties = feature.GetProperty("properties");
            var geometry = feature.GetProperty("geometry");
            features.Add(new LandFeature(
                properties.GetProperty("name").GetString() ?? "land",
                properties.GetProperty("iso3").GetString() ?? "---",
                ParseGeoJsonPolygons(geometry)));
        }

        return features;
    }

    private static List<GeoPolygon> ParseGeoJsonPolygons(JsonElement geometry)
    {
        var polygons = new List<GeoPolygon>();
        var geometryType = geometry.GetProperty("type").GetString();
        var coordinates = geometry.GetProperty("coordinates");

        if (geometryType == "Polygon")
        {
            polygons.Add(ParseGeoPolygon(coordinates));
            return polygons;
        }

        if (geometryType == "MultiPolygon")
        {
            foreach (var polygonElement in coordinates.EnumerateArray())
            {
                polygons.Add(ParseGeoPolygon(polygonElement));
            }
        }

        return polygons;
    }

    private static GeoPolygon ParseGeoPolygon(JsonElement polygonElement)
    {
        var rings = new List<List<Coordinate>>();
        foreach (var ringElement in polygonElement.EnumerateArray())
        {
            var ring = new List<Coordinate>();
            foreach (var pointElement in ringElement.EnumerateArray())
            {
                ring.Add(new Coordinate(
                    pointElement[0].GetDouble(),
                    pointElement[1].GetDouble()));
            }

            rings.Add(ring);
        }

        return new GeoPolygon(rings);
    }

    private async Task<HttpResponseMessage> SendWithRetryAsync(string url)
    {
        var delays = new[] { 5000, 10000, 20000, 30000, 45000, 60000 };
        for (var attempt = 0; attempt < delays.Length; attempt++)
        {
            var response = await _http.GetAsync(url);
            if ((int)response.StatusCode != 429)
            {
                response.EnsureSuccessStatusCode();
                return response;
            }

            response.Dispose();
            var delayMs = delays[attempt];
            Console.WriteLine($"    rate limited, retrying in {delayMs} ms");
            await Task.Delay(delayMs);
        }

        throw new HttpRequestException("Open-Meteo rate limited the requests after multiple retries.");
    }
}

internal sealed record ModelConfig(string DisplayName, string Key, string Endpoint);

internal sealed record RegionConfig(
    double MinLongitude,
    double MaxLongitude,
    double MinLatitude,
    double MaxLatitude,
    double LongitudeStep,
    double LatitudeStep)
{
    public List<Coordinate> BuildCoordinates()
    {
        var points = new List<Coordinate>();
        for (var lat = MaxLatitude; lat >= MinLatitude - 0.001; lat -= LatitudeStep)
        {
            for (var lon = MinLongitude; lon <= MaxLongitude + 0.001; lon += LongitudeStep)
            {
                points.Add(new Coordinate(Math.Round(lon, 4), Math.Round(lat, 4)));
            }
        }

        return points;
    }
}

internal sealed record Coordinate(double Longitude, double Latitude);

internal sealed record GeoPolygon(IReadOnlyList<IReadOnlyList<Coordinate>> Rings);

internal sealed record LandFeature(string Name, string Iso3, IReadOnlyList<GeoPolygon> Polygons);

internal sealed class ModelForecast
{
    private readonly Dictionary<DateTimeOffset, int> _timeIndex;
    private readonly Dictionary<(double Latitude, double Longitude), LocationForecast> _byCoordinate;
    private readonly double[] _latitudes;
    private readonly double[] _longitudes;

    public ModelForecast(ModelConfig model, IReadOnlyList<LocationForecast> locations, IReadOnlyList<DateTimeOffset> hourlyTimes, DateTimeOffset modelRunUtc)
    {
        Model = model;
        Locations = locations;
        HourlyTimes = hourlyTimes;
        ModelRunUtc = modelRunUtc;

        _timeIndex = hourlyTimes.Select((time, index) => (time, index)).ToDictionary(item => item.time, item => item.index);
        _byCoordinate = locations.ToDictionary(
            location => (Math.Round(location.Latitude, 4), Math.Round(location.Longitude, 4)),
            location => location);
        _latitudes = locations.Select(location => location.Latitude).Distinct().OrderByDescending(value => value).ToArray();
        _longitudes = locations.Select(location => location.Longitude).Distinct().OrderBy(value => value).ToArray();
    }

    public ModelConfig Model { get; }
    public IReadOnlyList<LocationForecast> Locations { get; }
    public IReadOnlyList<DateTimeOffset> HourlyTimes { get; }
    public DateTimeOffset ModelRunUtc { get; }

    public GridSnapshot BuildGridForSlot(DateTimeOffset slotTime)
    {
        if (!_timeIndex.TryGetValue(slotTime, out var index))
        {
            throw new KeyNotFoundException($"Forecast time {slotTime:o} is not available for {Model.DisplayName}.");
        }

        var pressure = new double[_latitudes.Length, _longitudes.Length];
        var minPressure = double.PositiveInfinity;
        var maxPressure = double.NegativeInfinity;

        for (var row = 0; row < _latitudes.Length; row++)
        {
            for (var col = 0; col < _longitudes.Length; col++)
            {
                var key = (Math.Round(_latitudes[row], 4), Math.Round(_longitudes[col], 4));
                var location = _byCoordinate[key];
                var p = location.PressureMsl[index];
                pressure[row, col] = p;

                if (!double.IsNaN(p))
                {
                    minPressure = Math.Min(minPressure, p);
                    maxPressure = Math.Max(maxPressure, p);
                }
            }
        }

        return new GridSnapshot(_latitudes, _longitudes, pressure, minPressure, maxPressure);
    }
}

internal sealed record LocationForecast(
    double Latitude,
    double Longitude,
    IReadOnlyList<DateTimeOffset> HourlyTimes,
    IReadOnlyList<double> PressureMsl);

internal sealed record GridSnapshot(
    IReadOnlyList<double> Latitudes,
    IReadOnlyList<double> Longitudes,
    double[,] Pressure,
    double MinPressure,
    double MaxPressure);

internal sealed record ForecastSlot(string Id, string Label, DateTimeOffset ForecastTimeJst);

internal sealed record Manifest(
    string Title,
    string GeneratedAt,
    string Timezone,
    string DataSource,
    string Note,
    IReadOnlyList<ManifestSlot> Slots);

internal sealed record ManifestSlot(
    string Id,
    string Label,
    string ForecastTime,
    IReadOnlyList<ManifestSlotModel> Models);

internal sealed record ManifestSlotModel(
    string Key,
    string Name,
    string ImagePath,
    string ForecastTime,
    string ModelRunTime);
