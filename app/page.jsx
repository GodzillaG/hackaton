"use client";

import { useEffect, useState } from "react";

const ANALYZE_ENDPOINT = "/api/analyze";
const HEALTH_ENDPOINT = "/api/health";
const MAX_UPLOAD_SIDE = 1600;

const SAMPLE_IMAGES = [
  {
    label: "Пример 1",
    src: "/test_images/red_ao_dai_full_body_pd.jpg",
    fileName: "red_ao_dai_full_body_pd.jpg",
    studentId: "DEMO-001"
  },
  {
    label: "Пример 2",
    src: "/test_images/woman_garden_full_length_cc0.jpg",
    fileName: "woman_garden_full_length_cc0.jpg",
    studentId: "DEMO-002"
  },
  {
    label: "Пример 3",
    src: "/test_images/union_officer_front_pd.jpg",
    fileName: "union_officer_front_pd.jpg",
    studentId: "DEMO-003"
  },
  {
    label: "Пример 4",
    src: "/test_images/union_officer_sword_pd.jpg",
    fileName: "union_officer_sword_pd.jpg",
    studentId: "DEMO-004"
  }
];

function formatMetricValue(metric) {
  const suffix = metric.unit === "deg" ? "°" : "%";
  return `${metric.value}${suffix}`;
}

function formatThreshold(metric) {
  const suffix = metric.unit === "deg" ? "°" : "%";
  return `${metric.threshold}${suffix}`;
}

function formatTimestamp(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatEngine(value) {
  if (value === "mediapipe_tasks_lite") return "ИИ-поза";
  if (value === "opencv_silhouette") return "Контур";
  return "Анализ";
}

function makeFile(blob, fileName) {
  if (typeof File === "function") {
    return new File([blob], fileName, { type: blob.type || "image/jpeg" });
  }
  blob.name = fileName;
  return blob;
}

async function prepareImageFile(sourceFile) {
  if (!sourceFile?.type?.startsWith("image/")) {
    return sourceFile;
  }

  const objectUrl = URL.createObjectURL(sourceFile);

  try {
    const image = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Браузер не смог прочитать это изображение."));
      img.src = objectUrl;
    });

    const scale = Math.min(1, MAX_UPLOAD_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;

    const context = canvas.getContext("2d", { alpha: false });
    if (!context) {
      throw new Error("Браузер не смог подготовить изображение.");
    }
    context.drawImage(image, 0, 0, width, height);

    const jpegBlob = await new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("Не удалось подготовить JPEG."))),
        "image/jpeg",
        0.9
      );
    });

    const baseName = (sourceFile.name || "upload").replace(/\.[^.]+$/, "");
    return makeFile(jpegBlob, `${baseName}.jpg`);
  } catch (error) {
    const name = sourceFile.name || "";
    if (/\.(heic|heif)$/i.test(name)) {
      throw new Error("HEIC/HEIF не прочитан браузером. Выберите JPG/PNG или включите совместимый формат камеры.");
    }
    throw error;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export default function Home() {
  const [apiStatus, setApiStatus] = useState("checking");
  const [studentId, setStudentId] = useState("");
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    let isActive = true;
    let controller = null;

    async function checkApi() {
      controller?.abort();
      controller = new AbortController();

      try {
        const response = await fetch(HEALTH_ENDPOINT, {
          signal: controller.signal
        });
        if (isActive) setApiStatus(response.ok ? "online" : "offline");
      } catch {
        if (isActive) setApiStatus("offline");
      }
    }

    checkApi();
    const intervalId = window.setInterval(checkApi, 7000);

    return () => {
      isActive = false;
      window.clearInterval(intervalId);
      controller?.abort();
    };
  }, []);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  function setSelectedFile(nextFile, nextStudentId = studentId) {
    setError("");
    setResult(null);
    setFile(nextFile || null);
    setStudentId(nextStudentId);

    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(nextFile ? URL.createObjectURL(nextFile) : "");
  }

  async function onFileChange(event) {
    const nextFile = event.target.files?.[0];
    event.target.value = "";

    if (!nextFile) {
      return;
    }

    try {
      setError("");
      const preparedFile = await prepareImageFile(nextFile);
      setSelectedFile(preparedFile);
      await analyzeFile(preparedFile, studentId);
    } catch (fileError) {
      setError(fileError.message || "Не удалось подготовить изображение.");
    }
  }

  async function useSampleImage(sample) {
    setError("");
    setResult(null);

    try {
      const response = await fetch(sample.src);
      if (!response.ok) {
        throw new Error("Не удалось загрузить пример.");
      }

      const blob = await response.blob();
      const sampleFile = makeFile(blob, sample.fileName);
      const preparedFile = await prepareImageFile(sampleFile);
      const sampleStudentId = sample.studentId;

      setSelectedFile(preparedFile, sampleStudentId);
      await analyzeFile(preparedFile, sampleStudentId);
    } catch (sampleError) {
      setError(sampleError.message || "Пример недоступен.");
    }
  }

  async function analyzeFile(nextFile, nextStudentId) {
    if (!nextFile) {
      setError("Добавьте фото ученика перед анализом.");
      return;
    }

    const formData = new FormData();
    formData.append("image", nextFile, nextFile.name || "upload.jpg");
    formData.append("student_id", String(nextStudentId || "").trim() || "unknown");

    setIsLoading(true);
    setError("");
    setResult(null);

    try {
      const response = await fetch(ANALYZE_ENDPOINT, {
        method: "POST",
        body: formData
      });
      const payload = await response.json().catch(() => ({
        error: "Сервер анализа вернул некорректный ответ."
      }));

      if (!response.ok) {
        if (response.status === 503) setApiStatus("offline");
        throw new Error(payload.error || "Сервер анализа вернул ошибку.");
      }

      setApiStatus("online");
      setResult(payload);
    } catch (requestError) {
      setError(requestError.message || "Не удалось отправить фото.");
    } finally {
      setIsLoading(false);
    }
  }

  async function analyzePhoto(event) {
    event.preventDefault();
    await analyzeFile(file, studentId);
  }

  function resetScan() {
    setFile(null);
    setResult(null);
    setError("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl("");
  }

  function downloadJson() {
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], {
      type: "application/json"
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${result.report_id || "screening-report"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="appShell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark">S</div>
          <div>
            <p className="eyebrow">Скрининг осанки</p>
            <h1>ScoliScan School</h1>
          </div>
        </div>
        <div className={`statusPill ${apiStatus}`}>
          <span />
          {apiStatus === "online" ? "Готов" : apiStatus === "offline" ? "Нет связи" : "Проверка"}
        </div>
      </header>

      <section className="workspace">
        <form className="panel capturePanel" onSubmit={analyzePhoto}>
          <div className="panelHeader">
            <div>
              <p className="eyebrow">Новый скрининг</p>
              <h2>Фото ученика</h2>
            </div>
            <button className="ghostButton" type="button" onClick={resetScan}>
              Сброс
            </button>
          </div>

          <label className="fieldLabel" htmlFor="studentId">
            ID ученика
          </label>
          <input
            id="studentId"
            className="textInput"
            value={studentId}
            onChange={(event) => setStudentId(event.target.value)}
            placeholder="Например: 7B-014"
          />

          <div className="sampleBlock">
            <div className="sampleHeader">
              <span>Примеры</span>
              <a href="/test_images/SOURCES.md" target="_blank">
                источники
              </a>
            </div>
            <div className="sampleGrid">
              {SAMPLE_IMAGES.map((sample) => (
                <button
                  className="sampleButton"
                  disabled={isLoading}
                  key={sample.src}
                  type="button"
                  onClick={() => useSampleImage(sample)}
                >
                  <img src={sample.src} alt="" />
                  <span>{sample.label}</span>
                </button>
              ))}
            </div>
          </div>

          <label className={`photoDrop ${previewUrl ? "hasPreview" : ""}`} htmlFor="galleryInput">
            {previewUrl ? (
              <img src={previewUrl} alt="Выбранное фото" />
            ) : (
              <div className="emptyPreview">
                <div className="scanFrame">
                  <span className="scanHead" />
                  <span className="scanBody" />
                  <span className="scanLine shoulder" />
                  <span className="scanLine hip" />
                </div>
                <strong>Выбрать фото</strong>
                <small>JPG / PNG</small>
              </div>
            )}
          </label>
          <input
            id="cameraInput"
            className="fileInput"
            type="file"
            accept="image/*"
            capture="environment"
            onChange={onFileChange}
          />
          <input
            id="galleryInput"
            className="fileInput"
            type="file"
            accept="image/*"
            onChange={onFileChange}
          />

          {error ? <div className="errorBox">{error}</div> : null}

          <div className="actionRow">
            <label className="secondaryButton" htmlFor="cameraInput">
              Камера
            </label>
            <label className="secondaryButton" htmlFor="galleryInput">
              Галерея
            </label>
            <button className="primaryButton" type="submit" disabled={isLoading || !file}>
              {isLoading ? "Анализ..." : "Запустить анализ"}
            </button>
          </div>
        </form>

        <section className="panel resultPanel">
          {result ? <ResultView result={result} onDownload={downloadJson} /> : <EmptyResult />}
        </section>
      </section>
    </main>
  );
}

function EmptyResult() {
  return (
    <div className="emptyResult">
      <div className="emptyDial">
        <span />
      </div>
      <p className="eyebrow">Результат</p>
      <h2>Нет активного отчёта</h2>
      <p>Готов к следующему скринингу.</p>
    </div>
  );
}

function ResultView({ result, onDownload }) {
  const riskClass = `risk-${result.risk?.accent || "gray"}`;
  const angle = Math.min(100, Math.max(0, result.risk?.score || 0)) * 3.6;

  return (
    <div className={`resultView ${riskClass}`}>
      <div className="resultHeader">
        <div>
          <p className="eyebrow">Отчёт {formatTimestamp(result.timestamp)}</p>
          <h2>{result.risk.label}</h2>
          <p>{result.risk.headline}</p>
        </div>
        <div className="riskGauge" style={{ "--angle": `${angle}deg` }}>
          <span>{result.risk.score}%</span>
        </div>
      </div>

      <div className="summaryGrid">
        <SummaryTile label="Флаги" value={`${result.risk.finding_count}/${result.risk.total_metrics}`} />
        <SummaryTile label="Качество" value={`${Math.round((result.quality_score || 0) * 100)}%`} />
        <SummaryTile label="Метод" value={formatEngine(result.analysis_engine)} compact />
      </div>

      <div className="contentGrid">
        <div className="metricsBlock">
          <div className="sectionHeader">
            <h3>Метрики</h3>
            <span>{result.student_id}</span>
          </div>
          <div className="metricList">
            {result.metric_cards.map((metric) => (
              <MetricRow key={metric.key} metric={metric} />
            ))}
          </div>
        </div>

        <div className="recommendationBlock">
          <div className="sectionHeader">
            <h3>Действия</h3>
            <button className="ghostButton" type="button" onClick={onDownload}>
              Скачать
            </button>
          </div>
          <ol className="recommendations">
            {result.recommendations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </div>
      </div>

      {result.overlay_image ? (
        <figure className="overlayFigure">
          <img src={result.overlay_image} alt="Разметка анализа" />
          <figcaption>{result.report_id}</figcaption>
        </figure>
      ) : null}
    </div>
  );
}

function SummaryTile({ label, value, compact = false }) {
  return (
    <div className={`summaryTile ${compact ? "compact" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MetricRow({ metric }) {
  const width = `${Math.min(100, Math.round((metric.severity_ratio || 0) * 100))}%`;

  return (
    <article className={`metricItem ${metric.triggered ? "attention" : ""}`}>
      <div className="metricTop">
        <div>
          <h4>{metric.title}</h4>
          <p>{metric.description}</p>
        </div>
        <strong>{formatMetricValue(metric)}</strong>
      </div>
      <div className="meter">
        <span className="meterFill" style={{ width }} />
      </div>
      <div className="metricFoot">
        <span>{metric.triggered ? "Выше порога" : "В норме"}</span>
        <span>Порог {formatThreshold(metric)}</span>
      </div>
    </article>
  );
}
