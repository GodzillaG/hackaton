"use client";

import { useEffect, useRef, useState } from "react";
import { buildHumanReportText, reportTextFileName } from "./reportExport.mjs";

const ANALYZE_ENDPOINT = "/api/analyze";
const HEALTH_ENDPOINT = "/api/health";
const AUTH_TOKEN_KEY = "scolioscan_session_token";
const REPORTS_ENDPOINT = "/api/reports";
const BILLING_ENDPOINT = "/api/billing";
const MAX_UPLOAD_SIDE = 1600;
const DEFAULT_BILLING_STATUS = {
  advanced_enabled: false,
  subscription: null,
  plan: { id: "free", name: "Free" },
  usage: { basic: 0, advanced: 0 },
  limits: { basic: 5, advanced: 0 },
  remaining: { basic: 5, advanced: 0 }
};

const BASIC_CAPTURE_STEP = {
  key: "single",
  fieldName: "image",
  label: "Basic",
  title: "Базовое фото",
  hint: "Один снимок в полный рост спереди, камера ровно на уровне середины корпуса."
};

const VIEW_STEPS = [
  {
    key: "front",
    fieldName: "image_front",
    label: "Спереди",
    title: "Вид спереди",
    hint: "Стойка прямо, руки свободно вдоль тела, плечи и таз в кадре."
  },
  {
    key: "back",
    fieldName: "image_back",
    label: "Спина",
    title: "Вид со спины",
    hint: "Спина к камере, стопы ровно, линия плеч и таза видна полностью."
  },
  {
    key: "left",
    fieldName: "image_left",
    label: "Левый бок",
    title: "Левый бок",
    hint: "Левый бок к камере, голова, плечи, таз и стопы попадают в кадр."
  },
  {
    key: "right",
    fieldName: "image_right",
    label: "Правый бок",
    title: "Правый бок",
    hint: "Правый бок к камере, корпус не развёрнут, камера на уровне середины тела."
  },
  {
    key: "adams",
    fieldName: "image_adams",
    label: "Адамс",
    title: "Тест Адамса",
    hint: "Снимок со спины в наклоне вперёд, руки свободно вниз, спина хорошо освещена."
  }
];

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

function createEmptyCaptureMap() {
  return Object.fromEntries(VIEW_STEPS.map((step) => [step.key, null]));
}

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
  if (value === "multi_view") return "Протокол";
  return "Анализ";
}

function formatRiskLevel(value) {
  if (value === "high") return "Высокий";
  if (value === "moderate") return "Средний";
  if (value === "low") return "Низкий";
  return "Повтор";
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
  const [authStatus, setAuthStatus] = useState("checking");
  const [authUser, setAuthUser] = useState(null);
  const [authToken, setAuthToken] = useState("");
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ username: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [isAuthLoading, setIsAuthLoading] = useState(false);
  const [reports, setReports] = useState([]);
  const [isReportsLoading, setIsReportsLoading] = useState(false);
  const [plans, setPlans] = useState([]);
  const [billing, setBilling] = useState(DEFAULT_BILLING_STATUS);
  const [isBillingLoading, setIsBillingLoading] = useState(false);
  const [schoolCode, setSchoolCode] = useState("");
  const [verificationStudentId, setVerificationStudentId] = useState("");
  const [studentId, setStudentId] = useState("");
  const [analysisMode, setAnalysisMode] = useState("basic");
  const [basicCapture, setBasicCapture] = useState(null);
  const [basicPreviewUrl, setBasicPreviewUrl] = useState("");
  const [captures, setCaptures] = useState(createEmptyCaptureMap);
  const [previewUrls, setPreviewUrls] = useState({});
  const [activeView, setActiveView] = useState(VIEW_STEPS[0].key);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const previewUrlsRef = useRef({});
  const basicPreviewUrlRef = useRef("");

  const activeStep = VIEW_STEPS.find((step) => step.key === activeView) || VIEW_STEPS[0];
  const completedCount = VIEW_STEPS.filter((step) => captures[step.key]).length;
  const isProtocolReady = completedCount === VIEW_STEPS.length;
  const activePreviewUrl = previewUrls[activeStep.key] || "";
  const activePlan = billing?.plan || { id: "free", name: "Free" };
  const remainingBasic = Number(billing?.remaining?.basic ?? 0);
  const remainingAdvanced = Number(billing?.remaining?.advanced ?? 0);
  const hasBasicAccess = remainingBasic > 0;
  const hasAdvancedAccess = remainingAdvanced > 0;

  useEffect(() => {
    const savedToken = window.localStorage.getItem(AUTH_TOKEN_KEY);
    if (!savedToken) {
      setAuthStatus("guest");
      return;
    }

    let isActive = true;

    async function restoreSession() {
      try {
        const response = await fetch("/api/auth/me", {
          headers: { Authorization: `Bearer ${savedToken}` }
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || "Сессия истекла.");
        if (!isActive) return;
        setAuthToken(savedToken);
        setAuthUser(payload.user);
        setBilling(payload.billing || DEFAULT_BILLING_STATUS);
        setAuthStatus("authenticated");
        await Promise.all([loadReports(savedToken), loadBilling(savedToken)]);
      } catch {
        window.localStorage.removeItem(AUTH_TOKEN_KEY);
        if (isActive) {
          setAuthStatus("guest");
          setAuthToken("");
          setAuthUser(null);
          setBilling(DEFAULT_BILLING_STATUS);
        }
      }
    }

    restoreSession();

    return () => {
      isActive = false;
    };
  }, []);

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
      Object.values(previewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
      if (basicPreviewUrlRef.current) URL.revokeObjectURL(basicPreviewUrlRef.current);
    };
  }, []);

  function setBasicFile(nextFile) {
    setError("");
    setResult(null);
    setBasicCapture(nextFile || null);

    setBasicPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      const nextUrl = nextFile ? URL.createObjectURL(nextFile) : "";
      basicPreviewUrlRef.current = nextUrl;
      return nextUrl;
    });
  }

  function setViewFile(viewKey, nextFile) {
    setError("");
    setResult(null);
    setCaptures((current) => ({ ...current, [viewKey]: nextFile || null }));

    setPreviewUrls((current) => {
      if (current[viewKey]) URL.revokeObjectURL(current[viewKey]);
      const nextUrl = nextFile ? URL.createObjectURL(nextFile) : "";
      const nextUrls = { ...current };
      if (nextUrl) {
        nextUrls[viewKey] = nextUrl;
      } else {
        delete nextUrls[viewKey];
      }
      previewUrlsRef.current = nextUrls;
      return nextUrls;
    });
  }

  function authHeaders(token = authToken) {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  function clearSession() {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    setAuthStatus("guest");
    setAuthToken("");
    setAuthUser(null);
    setReports([]);
    setBilling(DEFAULT_BILLING_STATUS);
    setResult(null);
  }

  async function loadBilling(token = authToken) {
    if (!token) return;
    setIsBillingLoading(true);
    try {
      const [plansResponse, statusResponse] = await Promise.all([
        fetch(`${BILLING_ENDPOINT}/plans`, { cache: "no-store" }),
        fetch(`${BILLING_ENDPOINT}/status`, {
          headers: authHeaders(token),
          cache: "no-store"
        })
      ]);

      const plansPayload = await plansResponse.json().catch(() => ({ plans: [] }));
      const statusPayload = await statusResponse.json().catch(() => DEFAULT_BILLING_STATUS);
      if (statusResponse.status === 401) {
        clearSession();
        return;
      }
      if (plansResponse.ok) setPlans(plansPayload.plans || []);
      if (statusResponse.ok) setBilling(statusPayload || DEFAULT_BILLING_STATUS);
    } finally {
      setIsBillingLoading(false);
    }
  }

  async function loadReports(token = authToken) {
    if (!token) return;
    setIsReportsLoading(true);
    try {
      const response = await fetch(REPORTS_ENDPOINT, {
        headers: authHeaders(token)
      });
      const payload = await response.json().catch(() => ({ reports: [] }));
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (response.ok) setReports(payload.reports || []);
    } finally {
      setIsReportsLoading(false);
    }
  }

  async function submitAuth(event) {
    event.preventDefault();
    setIsAuthLoading(true);
    setAuthError("");

    try {
      const response = await fetch(`/api/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(authForm)
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Не удалось войти.");

      window.localStorage.setItem(AUTH_TOKEN_KEY, payload.token);
      setAuthToken(payload.token);
      setAuthUser(payload.user);
      setBilling(payload.billing || DEFAULT_BILLING_STATUS);
      setAuthStatus("authenticated");
      await Promise.all([loadReports(payload.token), loadBilling(payload.token)]);
    } catch (loginError) {
      setAuthError(loginError.message || "Не удалось войти.");
    } finally {
      setIsAuthLoading(false);
    }
  }

  async function logoutUser() {
    if (authToken) {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: authHeaders()
      }).catch(() => {});
    }
    clearSession();
    resetScan();
  }

  async function activatePlan(plan) {
    if (!plan || !authToken) return;
    if (plan.id !== "plus") {
      setError("Corporate доступ активируется через подтверждение статуса ученика.");
      return;
    }
    setIsBillingLoading(true);
    setError("");

    try {
      const response = await fetch(`${BILLING_ENDPOINT}/checkout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({ plan_id: plan.id })
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (!response.ok) throw new Error(payload.error || "Не удалось оформить тариф.");
      setBilling(payload);
    } catch (billingError) {
      setError(billingError.message || "Не удалось оформить тариф.");
    } finally {
      setIsBillingLoading(false);
    }
  }

  async function verifyStudentStatus() {
    if (!authToken) return;
    setIsBillingLoading(true);
    setError("");

    try {
      const response = await fetch(`${BILLING_ENDPOINT}/verify-student`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        },
        body: JSON.stringify({
          school_code: schoolCode,
          student_id: verificationStudentId || studentId
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (!response.ok) throw new Error(payload.error || "Не удалось подтвердить статус ученика.");
      setBilling(payload);
      if (!studentId && (verificationStudentId || "").trim()) setStudentId(verificationStudentId.trim());
    } catch (verificationError) {
      setError(verificationError.message || "Не удалось подтвердить статус ученика.");
    } finally {
      setIsBillingLoading(false);
    }
  }

  async function openReport(reportId) {
    setError("");
    setIsReportsLoading(true);

    try {
      const response = await fetch(`${REPORTS_ENDPOINT}/${encodeURIComponent(reportId)}`, {
        headers: authHeaders()
      });
      const payload = await response.json().catch(() => ({
        error: "Сервер вернул некорректный отчёт."
      }));
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (!response.ok) throw new Error(payload.error || "Не удалось открыть отчёт.");
      setResult(payload);
    } catch (reportError) {
      setError(reportError.message || "Не удалось открыть отчёт.");
    } finally {
      setIsReportsLoading(false);
    }
  }

  function setProtocolFiles(nextCaptures, nextStudentId = studentId) {
    Object.values(previewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
    const nextPreviewUrls = {};

    for (const step of VIEW_STEPS) {
      if (nextCaptures[step.key]) {
        nextPreviewUrls[step.key] = URL.createObjectURL(nextCaptures[step.key]);
      }
    }

    setError("");
    setResult(null);
    setCaptures(nextCaptures);
    setStudentId(nextStudentId);
    setPreviewUrls(nextPreviewUrls);
    previewUrlsRef.current = nextPreviewUrls;
  }

  function goToNextMissingView(currentKey = activeStep.key, nextCaptures = captures) {
    const currentIndex = VIEW_STEPS.findIndex((step) => step.key === currentKey);
    const ordered = [...VIEW_STEPS.slice(currentIndex + 1), ...VIEW_STEPS.slice(0, currentIndex + 1)];
    const nextMissing = ordered.find((step) => !nextCaptures[step.key]);
    if (nextMissing) setActiveView(nextMissing.key);
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
      const nextCaptures = { ...captures, [activeStep.key]: preparedFile };
      setViewFile(activeStep.key, preparedFile);
      goToNextMissingView(activeStep.key, nextCaptures);
    } catch (fileError) {
      setError(fileError.message || "Не удалось подготовить изображение.");
    }
  }

  async function onBasicFileChange(event) {
    const nextFile = event.target.files?.[0];
    event.target.value = "";

    if (!nextFile) {
      return;
    }

    try {
      setError("");
      const preparedFile = await prepareImageFile(nextFile);
      setBasicFile(preparedFile);
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

      if (analysisMode === "advanced") {
        const nextCaptures = {};

        for (const step of VIEW_STEPS) {
          const sampleFile = makeFile(blob, `${step.key}_${sample.fileName}`);
          nextCaptures[step.key] = await prepareImageFile(sampleFile);
        }

        setProtocolFiles(nextCaptures, sample.studentId);
        if (hasAdvancedAccess) {
          await analyzeProtocol(nextCaptures, sample.studentId);
        } else {
          setError("Для запуска Advanced-анализа оформите тариф.");
        }
        return;
      }

      const sampleFile = makeFile(blob, sample.fileName);
      const preparedFile = await prepareImageFile(sampleFile);
      setBasicFile(preparedFile);
      setStudentId(sample.studentId);
      await analyzeBasic(preparedFile, sample.studentId);
    } catch (sampleError) {
      setError(sampleError.message || "Пример недоступен.");
    }
  }

  async function analyzeBasic(nextCapture = basicCapture, nextStudentId = studentId) {
    if (!nextCapture) {
      setError("Добавьте фото для базового анализа.");
      return;
    }
    if (!hasBasicAccess) {
      setError("Лимит Basic-анализов на текущем уровне закончился.");
      return;
    }

    const formData = new FormData();
    formData.append(BASIC_CAPTURE_STEP.fieldName, nextCapture, nextCapture.name || "basic.jpg");
    formData.append("student_id", String(nextStudentId || "").trim() || "unknown");

    await submitAnalyzeForm(formData, "Не удалось отправить фото.");
  }

  async function analyzeProtocol(nextCaptures = captures, nextStudentId = studentId) {
    const missingViews = VIEW_STEPS.filter((step) => !nextCaptures[step.key]);
    if (missingViews.length) {
      setError(`Добавьте все ракурсы: ${missingViews.map((step) => step.label).join(", ")}.`);
      setActiveView(missingViews[0].key);
      return;
    }

    if (!hasAdvancedAccess) {
      setError("На текущем уровне нет доступных Advanced-анализов.");
      return;
    }

    const formData = new FormData();
    for (const step of VIEW_STEPS) {
      const file = nextCaptures[step.key];
      formData.append(step.fieldName, file, file.name || `${step.key}.jpg`);
    }
    formData.append("student_id", String(nextStudentId || "").trim() || "unknown");

    await submitAnalyzeForm(formData, "Не удалось отправить протокол.");
  }

  async function submitAnalyzeForm(formData, fallbackError) {
    setIsLoading(true);
    setError("");
    setResult(null);

    try {
      if (!authToken) {
        throw new Error("Войдите в аккаунт.");
      }

      const response = await fetch(ANALYZE_ENDPOINT, {
        method: "POST",
        headers: authHeaders(),
        body: formData
      });
      const payload = await response.json().catch(() => ({
        error: "Сервер анализа вернул некорректный ответ."
      }));

      if (!response.ok) {
        if (response.status === 503) setApiStatus("offline");
        if (response.status === 401) clearSession();
        if (response.status === 402 && payload.code === "advanced_required") {
          setAnalysisMode("advanced");
          await loadBilling(authToken);
        }
        throw new Error(payload.error || "Сервер анализа вернул ошибку.");
      }

      setApiStatus("online");
      setResult(payload);
      await Promise.all([loadReports(authToken), loadBilling(authToken)]);
    } catch (requestError) {
      setError(requestError.message || fallbackError);
    } finally {
      setIsLoading(false);
    }
  }

  async function submitAnalysis(event) {
    event.preventDefault();
    if (analysisMode === "advanced") {
      await analyzeProtocol();
    } else {
      await analyzeBasic();
    }
  }

  function resetScan() {
    Object.values(previewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
    if (basicPreviewUrlRef.current) URL.revokeObjectURL(basicPreviewUrlRef.current);
    previewUrlsRef.current = {};
    basicPreviewUrlRef.current = "";
    setBasicCapture(null);
    setBasicPreviewUrl("");
    setCaptures(createEmptyCaptureMap());
    setPreviewUrls({});
    setActiveView(VIEW_STEPS[0].key);
    setResult(null);
    setError("");
  }

  function switchAnalysisMode(nextMode) {
    setAnalysisMode(nextMode);
    setError("");
    setResult(null);
  }

  function downloadReport() {
    if (!result) return;
    const blob = new Blob([buildHumanReportText(result)], {
      type: "text/plain;charset=utf-8"
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = reportTextFileName(result);
    link.click();
    URL.revokeObjectURL(url);
  }

  if (authStatus !== "authenticated") {
    return (
      <main className="appShell">
        <header className="topbar">
          <div className="brand">
            <div className="brandMark">S</div>
            <div>
              <p className="eyebrow">Скрининг осанки</p>
              <h1>ScolioScan School</h1>
            </div>
          </div>
          <div className={`statusPill ${apiStatus}`}>
            <span />
            {apiStatus === "online" ? "Готов" : apiStatus === "offline" ? "Нет связи" : "Проверка"}
          </div>
          <a className="resourceLink" href="/learn/posture">
            Осанка
          </a>
        </header>
        <AuthScreen
          authError={authError}
          authForm={authForm}
          authMode={authMode}
          authStatus={authStatus}
          isAuthLoading={isAuthLoading}
          onChange={setAuthForm}
          onModeChange={setAuthMode}
          onSubmit={submitAuth}
        />
      </main>
    );
  }

  return (
    <main className="appShell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark">S</div>
          <div>
            <p className="eyebrow">Скрининг осанки</p>
            <h1>ScolioScan School</h1>
          </div>
        </div>
        <div className="topbarActions">
          <a className="resourceLink" href="/learn/posture">
            Осанка
          </a>
          <div className={`statusPill ${apiStatus}`}>
            <span />
            {apiStatus === "online" ? "Готов" : apiStatus === "offline" ? "Нет связи" : "Проверка"}
          </div>
          <div className={`planPill ${hasAdvancedAccess ? "active" : ""}`}>
            {activePlan.name || "Free"}
          </div>
          <div className="userPill">{authUser?.username}</div>
          <button className="ghostButton" type="button" onClick={logoutUser}>
            Выйти
          </button>
        </div>
      </header>

      <section className="workspace">
        <form className="panel capturePanel" onSubmit={submitAnalysis}>
          <div className="panelHeader">
            <div>
              <p className="eyebrow">Новый скрининг</p>
              <h2>{analysisMode === "advanced" ? "Advanced анализ" : "Basic анализ"}</h2>
            </div>
            <button className="ghostButton" type="button" onClick={resetScan}>
              Сброс
            </button>
          </div>

          <div className="analysisModeSwitch" aria-label="Режим анализа">
            <button
              className={analysisMode === "basic" ? "active" : ""}
              type="button"
              onClick={() => switchAnalysisMode("basic")}
            >
              <strong>Basic</strong>
              <span>1 фото</span>
            </button>
            <button
              className={analysisMode === "advanced" ? "active" : ""}
              type="button"
              onClick={() => switchAnalysisMode("advanced")}
            >
              <strong>Advanced</strong>
              <span>5 фото</span>
            </button>
          </div>

          <QuotaStrip billing={billing} />

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

          <ReportHistory reports={reports} isLoading={isReportsLoading} onOpen={openReport} />

          {analysisMode === "advanced" ? (
            <>
              <PricingPanel
                billing={billing}
                isLoading={isBillingLoading}
                onActivate={activatePlan}
                onSchoolCodeChange={setSchoolCode}
                onStudentIdChange={setVerificationStudentId}
                onVerifyStudent={verifyStudentStatus}
                plans={plans}
                schoolCode={schoolCode}
                studentId={verificationStudentId || studentId}
              />

              <div className="protocolProgress">
                <span>{completedCount}/5</span>
                <div className="progressTrack">
                  <span style={{ width: `${(completedCount / VIEW_STEPS.length) * 100}%` }} />
                </div>
              </div>

              <div className="viewStepper" aria-label="Ракурсы скрининга">
                {VIEW_STEPS.map((step, index) => (
                  <button
                    className={`viewStep ${activeStep.key === step.key ? "active" : ""} ${captures[step.key] ? "done" : ""}`}
                    key={step.key}
                    type="button"
                    onClick={() => setActiveView(step.key)}
                  >
                    <strong>{index + 1}</strong>
                    <span>{step.label}</span>
                  </button>
                ))}
              </div>

              <div className="activeViewHeader">
                <div>
                  <p className="eyebrow">{activeStep.label}</p>
                  <h3>{activeStep.title}</h3>
                </div>
                <span>{captures[activeStep.key] ? "Готово" : "Нужно фото"}</span>
              </div>
              <p className="viewHint">{activeStep.hint}</p>

              <label className={`photoDrop ${activePreviewUrl ? "hasPreview" : ""}`} htmlFor="galleryInput">
                {activePreviewUrl ? (
                  <img src={activePreviewUrl} alt={`Фото: ${activeStep.title}`} />
                ) : (
                  <div className="emptyPreview">
                    <div className={`scanFrame scanFrame-${activeStep.key}`}>
                      <span className="scanHead" />
                      <span className="scanBody" />
                      <span className="scanLine shoulder" />
                      <span className="scanLine hip" />
                    </div>
                    <strong>{activeStep.title}</strong>
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
            </>
          ) : (
            <>
              <div className="basicIntro">
                <div>
                  <strong>Базовый режим</strong>
                  <span>Быстрый анализ по одному фронтальному фото.</span>
                </div>
                <em>$0</em>
              </div>

              <div className="activeViewHeader">
                <div>
                  <p className="eyebrow">{BASIC_CAPTURE_STEP.label}</p>
                  <h3>{BASIC_CAPTURE_STEP.title}</h3>
                </div>
                <span>{basicCapture ? "Готово" : "Нужно фото"}</span>
              </div>
              <p className="viewHint">{BASIC_CAPTURE_STEP.hint}</p>

              <label className={`photoDrop ${basicPreviewUrl ? "hasPreview" : ""}`} htmlFor="basicGalleryInput">
                {basicPreviewUrl ? (
                  <img src={basicPreviewUrl} alt="Фото для базового анализа" />
                ) : (
                  <div className="emptyPreview">
                    <div className="scanFrame scanFrame-front">
                      <span className="scanHead" />
                      <span className="scanBody" />
                      <span className="scanLine shoulder" />
                      <span className="scanLine hip" />
                    </div>
                    <strong>{BASIC_CAPTURE_STEP.title}</strong>
                    <small>JPG / PNG</small>
                  </div>
                )}
              </label>
              <input
                id="basicCameraInput"
                className="fileInput"
                type="file"
                accept="image/*"
                capture="environment"
                onChange={onBasicFileChange}
              />
              <input
                id="basicGalleryInput"
                className="fileInput"
                type="file"
                accept="image/*"
                onChange={onBasicFileChange}
              />
            </>
          )}

          {error ? <div className="errorBox">{error}</div> : null}

          <div className="actionRow">
            <label className="secondaryButton" htmlFor={analysisMode === "advanced" ? "cameraInput" : "basicCameraInput"}>
              Камера
            </label>
            <label className="secondaryButton" htmlFor={analysisMode === "advanced" ? "galleryInput" : "basicGalleryInput"}>
              Галерея
            </label>
            <button
              className="primaryButton"
              type="submit"
              disabled={
                isLoading ||
                (analysisMode === "advanced" ? !isProtocolReady || !hasAdvancedAccess : !basicCapture || !hasBasicAccess)
              }
            >
              {isLoading ? "Анализ..." : "Запустить анализ"}
            </button>
          </div>
        </form>

        <section className="panel resultPanel">
          {result ? <ResultView result={result} onDownload={downloadReport} /> : <EmptyResult />}
        </section>
      </section>
    </main>
  );
}

function AuthScreen({
  authError,
  authForm,
  authMode,
  authStatus,
  isAuthLoading,
  onChange,
  onModeChange,
  onSubmit
}) {
  const isRegister = authMode === "register";

  return (
    <section className="authShell">
      <div className="authHero">
        <img src="/education/posture-hero.png" alt="" />
        <div className="authHeroContent">
          <p className="eyebrow">ScolioScan School</p>
          <h2>Быстрый скрининг осанки для школы без очередей</h2>
          <p>Basic для быстрых проверок, Plus для регулярного контроля и Corporate для школьных программ.</p>
        </div>
      </div>
      <form className="panel authPanel" onSubmit={onSubmit}>
        <div className="panelHeader">
          <div>
            <p className="eyebrow">Аккаунт</p>
            <h2>{isRegister ? "Регистрация" : "Вход"}</h2>
          </div>
          <div className="authModeSwitch">
            <button
              className={authMode === "login" ? "active" : ""}
              type="button"
              onClick={() => onModeChange("login")}
            >
              Вход
            </button>
            <button
              className={authMode === "register" ? "active" : ""}
              type="button"
              onClick={() => onModeChange("register")}
            >
              Новый
            </button>
          </div>
        </div>

        <label className="fieldLabel" htmlFor="authUsername">
          Логин
        </label>
        <input
          id="authUsername"
          className="textInput"
          autoComplete="username"
          value={authForm.username}
          onChange={(event) => onChange((current) => ({ ...current, username: event.target.value }))}
          placeholder="Ваш логин"
        />

        <label className="fieldLabel" htmlFor="authPassword">
          Пароль
        </label>
        <input
          id="authPassword"
          className="textInput"
          autoComplete={isRegister ? "new-password" : "current-password"}
          type="password"
          value={authForm.password}
          onChange={(event) => onChange((current) => ({ ...current, password: event.target.value }))}
          placeholder="Ваш пароль"
        />

        {authError ? <div className="errorBox">{authError}</div> : null}

        <button className="primaryButton authSubmit" type="submit" disabled={isAuthLoading || authStatus === "checking"}>
          {isAuthLoading ? "Проверка..." : isRegister ? "Создать аккаунт" : "Войти"}
        </button>
      </form>
    </section>
  );
}

function PricingPanel({
  billing,
  isLoading,
  onActivate,
  onSchoolCodeChange,
  onStudentIdChange,
  onVerifyStudent,
  plans,
  schoolCode,
  studentId
}) {
  const activePlanId = billing?.plan?.id || "free";
  const freePlan = plans.find((plan) => plan.id === "free");
  const plusPlan = plans.find((plan) => plan.id === "plus");
  const corporatePlan = plans.find((plan) => plan.id === "corporate");

  return (
    <section className="pricingPanel">
      <div className="pricingHeader">
        <div>
          <p className="eyebrow">Уровень доступа</p>
          <h3>{billing?.plan?.name || "Free"} · {formatQuotaLine(billing)}</h3>
        </div>
      </div>

      <div className="planGrid tierGrid">
        {[freePlan, plusPlan, corporatePlan].filter(Boolean).map((plan) => (
          <PlanCard
            activePlanId={activePlanId}
            isLoading={isLoading}
            key={plan.id}
            onActivate={onActivate}
            plan={plan}
          />
        ))}
      </div>

      <div className="studentVerifyPanel">
        <div className="pricingGroupTitle">
          <span>Подтвердить статус ученика</span>
          <small>для доступа по договору школы</small>
        </div>
        <div className="verifyGrid">
          <input
            className="textInput"
            value={schoolCode}
            onChange={(event) => onSchoolCodeChange(event.target.value)}
            placeholder="Код школы"
          />
          <input
            className="textInput"
            value={studentId}
            onChange={(event) => onStudentIdChange(event.target.value)}
            placeholder="ID ученика"
          />
          <button className="secondaryButton" disabled={isLoading} type="button" onClick={onVerifyStudent}>
            {isLoading ? "Проверка..." : "Подтвердить"}
          </button>
        </div>
      </div>
    </section>
  );
}

function PlanCard({ activePlanId, isLoading, onActivate, plan }) {
  const isActive = activePlanId === plan.id;
  const canActivate = plan.id === "plus" && !isActive;

  return (
    <article className={`planCard ${isActive ? "active" : ""}`}>
      <div>
        <span>{plan.audience_label}</span>
        <h4>{plan.name}</h4>
        <p>{plan.description}</p>
        <div className="quotaBadges">
          <em>{plan.basic_quota} Basic/мес</em>
          <em>{plan.advanced_quota} Advanced/мес</em>
        </div>
        {Array.isArray(plan.features) && plan.features.length ? (
          <ul className="planFeatures">
            {plan.features.map((feature) => (
              <li key={feature}>{feature}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="planCardBottom">
        <strong>{formatPlanPrice(plan)}</strong>
        {isActive ? (
          <span className="activePlanBadge">Текущий</span>
        ) : plan.id === "corporate" ? (
          <span className="activePlanBadge neutral">Код школы</span>
        ) : plan.id === "free" ? (
          <span className="activePlanBadge neutral">База</span>
        ) : (
          <button className="secondaryButton" disabled={isLoading || !canActivate} type="button" onClick={() => onActivate(plan)}>
            {isLoading ? "Оформление..." : "Подключить"}
          </button>
        )}
      </div>
    </article>
  );
}

function formatPlanPrice(plan) {
  return plan.price_label || `$${plan.price_usd}`;
}

function QuotaStrip({ billing }) {
  const plan = billing?.plan || { name: "Free" };
  return (
    <section className="quotaStrip">
      <div>
        <span>Уровень</span>
        <strong>{plan.name}</strong>
      </div>
      <QuotaMeter label="Basic" limit={billing?.limits?.basic} remaining={billing?.remaining?.basic} used={billing?.usage?.basic} />
      <QuotaMeter label="Advanced" limit={billing?.limits?.advanced} remaining={billing?.remaining?.advanced} used={billing?.usage?.advanced} />
    </section>
  );
}

function QuotaMeter({ label, limit = 0, remaining = 0, used = 0 }) {
  const safeLimit = Number(limit) || 0;
  const safeUsed = Number(used) || 0;
  const percent = safeLimit ? Math.min(100, Math.round((safeUsed / safeLimit) * 100)) : 100;

  return (
    <div className="quotaMeter">
      <span>{label}</span>
      <strong>{remaining}/{limit}</strong>
      <div className="quotaTrack">
        <i style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function formatQuotaLine(billing) {
  const basic = billing?.remaining?.basic ?? 0;
  const advanced = billing?.remaining?.advanced ?? 0;
  return `${basic} Basic, ${advanced} Advanced доступно`;
}

function EmptyResult() {
  return (
    <div className="emptyResult">
      <div className="emptyDial">
        <span />
      </div>
      <p className="eyebrow">Результат</p>
      <h2>Нет активного отчёта</h2>
      <p>Готов к базовому анализу по одному фото или Advanced-протоколу.</p>
    </div>
  );
}

function ReportHistory({ reports, isLoading, onOpen }) {
  return (
    <section className="historyBlock">
      <div className="sampleHeader">
        <span>История</span>
        <small>{isLoading ? "обновление" : `${reports.length}`}</small>
      </div>
      {reports.length ? (
        <div className="reportList">
          {reports.slice(0, 6).map((report) => (
            <button className="reportButton" key={report.report_id} type="button" onClick={() => onOpen(report.report_id)}>
              <div>
                <strong>{report.student_id}</strong>
                <span>{formatTimestamp(report.created_at)}</span>
              </div>
              <em className={`riskText riskText-${report.risk_level}`}>{formatRiskLevel(report.risk_level)}</em>
            </button>
          ))}
        </div>
      ) : (
        <p className="historyEmpty">Нет сохранённых отчётов</p>
      )}
    </section>
  );
}

function ResultView({ result, onDownload }) {
  const riskClass = `risk-${result.risk?.accent || "gray"}`;
  const angle = Math.min(100, Math.max(0, result.risk?.score || 0)) * 3.6;
  const isMultiView = result.mode === "multi_view" && Array.isArray(result.views);

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
        <SummaryTile label="Ракурсы" value={isMultiView ? `${result.views_completed}/${result.view_count}` : "1/1"} />
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
            <h3>Кратко</h3>
            <button className="ghostButton" type="button" onClick={onDownload}>
              Скачать отчёт
            </button>
          </div>
          <ol className="recommendations">
            {result.recommendations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </div>
      </div>

      {Array.isArray(result.care_plan) && result.care_plan.length > 0 ? (
        <CarePlan plan={result.care_plan} />
      ) : null}

      {isMultiView ? (
        <div className="viewResultGrid">
          {result.views.map((view) => (
            <ViewResultCard key={view.view_key} view={view} />
          ))}
        </div>
      ) : result.overlay_image ? (
        <figure className="overlayFigure">
          <img src={result.overlay_image} alt="Разметка анализа" />
          <figcaption>{result.report_id}</figcaption>
        </figure>
      ) : null}
    </div>
  );
}

function CarePlan({ plan }) {
  return (
    <section className="carePlanBlock">
      <div className="sectionHeader">
        <h3>План действий</h3>
        <span>{formatStepCount(plan.length)}</span>
      </div>
      <div className="carePlanGrid">
        {plan.map((item) => (
          <article className={`carePlanItem ${item.level || "neutral"}`} key={`${item.title}-${item.body}`}>
            <span>{formatCarePlanLevel(item.level)}</span>
            <h4>{item.title}</h4>
            <p>{item.body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function formatCarePlanLevel(level) {
  if (level === "urgent") return "Приоритет";
  if (level === "attention") return "Контроль";
  if (level === "ok") return "Ок";
  return "Повтор";
}

function formatStepCount(count) {
  if (count % 10 === 1 && count % 100 !== 11) return `${count} шаг`;
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) return `${count} шага`;
  return `${count} шагов`;
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

function ViewResultCard({ view }) {
  const isProfileView = view.view_role === "profile";

  return (
    <article className={`viewResultCard risk-${view.risk?.accent || "gray"}`}>
      <div className="viewResultTop">
        <div>
          <h4>{view.view_label}</h4>
          <span>{isProfileView ? "Профильный контроль" : view.risk.label}</span>
        </div>
        <strong>{isProfileView ? "—" : `${view.risk.score}%`}</strong>
      </div>
      {view.overlay_image ? <img src={view.overlay_image} alt={`Разметка: ${view.view_label}`} /> : null}
      <div className="viewResultMeta">
        <span>{Math.round((view.quality_score || 0) * 100)}%</span>
        <span>{formatEngine(view.analysis_engine)}</span>
      </div>
    </article>
  );
}
