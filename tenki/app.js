(function () {
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
  const manifestScriptUrl = window.TENKI_MANIFEST_URL || `./data/manifest.js?v=${Date.now()}`;

  let activeManifest = null;
  let activeSlotIndex = 0;
  let maxIndex = 0;
  let assetVersion = "";

  loadManifest()
    .then(function (manifest) {
      init(manifest);
    })
    .catch(function () {
      cards.innerHTML =
        '<div class="empty-state">Failed to load the latest manifest. Reload this page.</div>';
    });

  function loadManifest() {
    return new Promise(function (resolve, reject) {
      const script = document.createElement("script");
      script.src = manifestScriptUrl;
      script.async = true;
      script.onload = function () {
        if (window.TENKI_MANIFEST && Array.isArray(window.TENKI_MANIFEST.slots)) {
          resolve(window.TENKI_MANIFEST);
          return;
        }
        reject(new Error("Manifest payload missing"));
      };
      script.onerror = function () {
        reject(new Error("Manifest script failed to load"));
      };
      document.head.appendChild(script);
    });
  }

  function init(manifest) {
    activeManifest = manifest;
    activeSlotIndex = 0;
    maxIndex = manifest.slots.length - 1;
    assetVersion = encodeURIComponent(manifest.generatedAt || "");

    if (!Array.isArray(manifest.slots) || manifest.slots.length === 0) {
      cards.innerHTML =
        '<div class="empty-state">データがありません。`./generate.ps1` を実行してから開いてください。</div>';
      return;
    }

    generatedAt.textContent = formatIso(manifest.generatedAt);
    dataSource.textContent = manifest.dataSource || "-";
    manifestNote.textContent = manifest.note || "-";

    slotSlider.max = String(maxIndex);
    slotSlider.value = "0";
    slotStartLabel.textContent = formatSlotLabel(manifest.slots[0].forecastTime);
    slotEndLabel.textContent = formatSlotLabel(manifest.slots[maxIndex].forecastTime);

    slotSlider.addEventListener("input", onSlotInput);
    slotPrev.addEventListener("click", onPrevClick);
    slotNext.addEventListener("click", onNextClick);

    render();
    scheduleWindowPreload();
  }

  function onSlotInput() {
    activeSlotIndex = Number(slotSlider.value);
    render();
  }

  function onPrevClick() {
    activeSlotIndex = Math.max(0, activeSlotIndex - 1);
    render();
  }

  function onNextClick() {
    activeSlotIndex = Math.min(maxIndex, activeSlotIndex + 1);
    render();
  }

  function render() {
    if (!activeManifest) {
      return;
    }

    const slot = activeManifest.slots[activeSlotIndex] || activeManifest.slots[0];
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
    if (!activeManifest) {
      return;
    }

    const start = Math.max(0, centerIndex - radius);
    const end = Math.min(maxIndex, centerIndex + radius);

    for (let index = start; index <= end; index += 1) {
      const slot = activeManifest.slots[index];
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
      timeZone: activeManifest?.timezone || "Asia/Tokyo",
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
      timeZone: activeManifest?.timezone || "Asia/Tokyo",
    }).formatToParts(date);
    const get = function (type) {
      return parts.find((part) => part.type === type)?.value || "";
    };

    return `${get("month")}/${get("day")}(${get("weekday")}) ${get("hour")}:${get("minute")} JST`;
  }
})();
