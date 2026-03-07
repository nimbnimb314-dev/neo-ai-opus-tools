(function () {
  const manifest = window.TENKI_MANIFEST;
  const generatedAt = document.getElementById("generated-at");
  const dataSource = document.getElementById("data-source");
  const manifestNote = document.getElementById("manifest-note");
  const cards = document.getElementById("model-cards");
  const currentSlotLabel = document.getElementById("current-slot-label");
  const sliderSlotLabel = document.getElementById("slider-slot-label");
  const slotSlider = document.getElementById("slot-slider");
  const slotStartLabel = document.getElementById("slot-start-label");
  const slotEndLabel = document.getElementById("slot-end-label");
  const slotPrev = document.getElementById("slot-prev");
  const slotNext = document.getElementById("slot-next");
  const preloadState = new Map();
  const preloadRadius = 2;
  const assetVersion = encodeURIComponent(manifest?.generatedAt || "");

  if (!manifest || !Array.isArray(manifest.slots) || manifest.slots.length === 0) {
    cards.innerHTML =
      '<div class="empty-state">データがありません。`./generate.ps1` を実行してから開いてください。</div>';
    return;
  }

  generatedAt.textContent = formatIso(manifest.generatedAt);
  dataSource.textContent = manifest.dataSource || "-";
  manifestNote.textContent = manifest.note || "-";

  let activeSlotIndex = 0;
  const maxIndex = manifest.slots.length - 1;

  slotSlider.max = String(maxIndex);
  slotSlider.value = "0";
  slotStartLabel.textContent = formatSlotLabel(manifest.slots[0].forecastTime);
  slotEndLabel.textContent = formatSlotLabel(manifest.slots[maxIndex].forecastTime);

  slotSlider.addEventListener("input", function () {
    activeSlotIndex = Number(slotSlider.value);
    render();
  });

  slotPrev.addEventListener("click", function () {
    activeSlotIndex = Math.max(0, activeSlotIndex - 1);
    render();
  });

  slotNext.addEventListener("click", function () {
    activeSlotIndex = Math.min(maxIndex, activeSlotIndex + 1);
    render();
  });

  render();
  scheduleWindowPreload();

  function render() {
    const slot = manifest.slots[activeSlotIndex] || manifest.slots[0];
    currentSlotLabel.textContent = formatSlotLabel(slot.forecastTime);
    sliderSlotLabel.textContent = formatSlotLabel(slot.forecastTime);
    slotSlider.value = String(activeSlotIndex);
    slotPrev.disabled = activeSlotIndex === 0;
    slotNext.disabled = activeSlotIndex === maxIndex;
    preloadNearbySlots(activeSlotIndex, preloadRadius);

    cards.innerHTML = "";
    slot.models.forEach((model) => {
      const article = document.createElement("article");
      article.className = "model-card";
      article.innerHTML = [
        '<div class="model-card-header">',
        `<p class="section-kicker">${model.key.toUpperCase()}</p>`,
        `<h3>${model.name}</h3>`,
        `<p>予報時刻: ${formatIso(model.forecastTime)}</p>`,
        `<p>モデル更新時刻: ${formatIso(model.modelRunTime)}</p>`,
        "</div>",
        '<div class="image-wrap">',
        '<div class="image-frame">',
        `<img src="${buildImageUrl(model.imagePath)}" alt="${model.name} forecast map" width="1280" height="960" loading="eager" decoding="async" />`,
        "</div>",
        "</div>",
      ].join("");
      cards.appendChild(article);
    });
  }

  function scheduleWindowPreload() {
    const runner = function () {
      preloadNearbySlots(activeSlotIndex, preloadRadius);
    };

    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(runner, { timeout: 1200 });
      return;
    }

    window.setTimeout(runner, 150);
  }

  function preloadNearbySlots(centerIndex, radius) {
    const start = Math.max(0, centerIndex - radius);
    const end = Math.min(maxIndex, centerIndex + radius);

    for (let index = start; index <= end; index += 1) {
      const slot = manifest.slots[index];
      if (!slot || !Array.isArray(slot.models)) {
        continue;
      }
      slot.models.forEach((model) => {
        preloadImage(buildImageUrl(model.imagePath));
      });
    }
  }

  function preloadImage(src) {
    if (!src) {
      return Promise.resolve();
    }

    const existing = preloadState.get(src);
    if (existing) {
      return existing;
    }

    const promise = new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.loading = "eager";
      image.onload = function () {
        resolve();
      };
      image.onerror = function () {
        resolve();
      };
      image.src = src;
    });

    preloadState.set(src, promise);
    return promise;
  }

  function buildImageUrl(path) {
    if (!path) {
      return "";
    }

    if (!assetVersion) {
      return path;
    }

    const separator = path.includes("?") ? "&" : "?";
    return `${path}${separator}v=${assetVersion}`;
  }

  function formatIso(value) {
    if (!value) {
      return "-";
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return new Intl.DateTimeFormat("ja-JP", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: manifest.timezone || "Asia/Tokyo",
      timeZoneName: "short",
    }).format(date);
  }

  function formatSlotLabel(value) {
    if (!value) {
      return "-";
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }

    const parts = new Intl.DateTimeFormat("ja-JP", {
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: manifest.timezone || "Asia/Tokyo",
    }).formatToParts(date);
    const get = function (type) {
      return parts.find((part) => part.type === type)?.value || "";
    };

    return `${get("month")}/${get("day")}(${get("weekday")}) ${get("hour")}:${get("minute")} JST`;
  }
})();
