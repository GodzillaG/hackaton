function cleanValue(value, fallback = "не указано") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function formatDate(value) {
  if (!value) return "не указана";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    }).format(new Date(value));
  } catch {
    return String(value);
  }
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "не указано";
  return `${Math.round(number * 100)}%`;
}

function formatMetricValue(metric) {
  const suffix = metric?.unit === "deg" ? "°" : "%";
  return `${cleanValue(metric?.value, "0")}${suffix}`;
}

function formatMetricThreshold(metric) {
  const suffix = metric?.unit === "deg" ? "°" : "%";
  return `${cleanValue(metric?.threshold, "0")}${suffix}`;
}

function formatMode(result) {
  if (result?.mode === "multi_view") return "Advanced: 5 ракурсов";
  return "Basic: 1 фронтальное фото";
}

function formatEngine(value) {
  if (value === "mediapipe_tasks_lite") return "ИИ-поза MediaPipe";
  if (value === "opencv_silhouette") return "контурный анализ OpenCV";
  if (value === "multi_view") return "многоракурсный протокол";
  return cleanValue(value, "анализ изображения");
}

function formatStatus(triggered) {
  return triggered ? "требует внимания" : "в пределах контрольного уровня";
}

function humanConclusion(result) {
  const level = result?.risk?.level;
  const score = Number(result?.risk?.score ?? 0);

  if (!result?.landmarks_found) {
    return "Кадр не дал достаточно ориентиров для уверенного анализа. Лучше повторить съёмку при хорошем освещении и полном попадании тела в кадр.";
  }

  if (level === "high") {
    return `Выявлены выраженные признаки асимметрии. Итоговый риск: высокий, ${score}%. Результат стоит передать школьному медработнику или ортопеду для очной проверки.`;
  }

  if (level === "moderate") {
    return `Есть отдельные признаки асимметрии. Итоговый риск: средний, ${score}%. Рекомендуется повторить съёмку и провести контрольный школьный осмотр.`;
  }

  if (level === "low") {
    return `Критичных признаков риска не обнаружено. Итоговый риск: низкий, ${score}%. Результат можно сохранить как плановый скрининг.`;
  }

  return "Результат требует повторной проверки с новым снимком или полным протоколом.";
}

function numberedList(items) {
  if (!Array.isArray(items) || items.length === 0) return ["- нет данных"];
  return items.map((item, index) => `${index + 1}. ${item}`);
}

function carePlanLines(plan) {
  if (!Array.isArray(plan) || plan.length === 0) return ["- нет отдельного плана действий"];
  return plan.map((item, index) => {
    const title = cleanValue(item?.title, "Шаг");
    const body = cleanValue(item?.body, "");
    return `${index + 1}. ${title}: ${body}`;
  });
}

function metricLines(metrics) {
  if (!Array.isArray(metrics) || metrics.length === 0) return ["- метрики для этого ракурса не применяются"];

  return metrics.map((metric) => {
    const title = cleanValue(metric?.title, "Метрика");
    const description = cleanValue(metric?.description, "");
    return [
      `- ${title}: ${formatMetricValue(metric)} при контрольном уровне ${formatMetricThreshold(metric)}`,
      `  Статус: ${formatStatus(metric?.triggered)}.`,
      description ? `  Что означает: ${description}.` : ""
    ].filter(Boolean).join("\n");
  });
}

function viewLines(views) {
  if (!Array.isArray(views) || views.length === 0) return [];

  const lines = ["", "РАКУРСЫ ADVANCED"];
  views.forEach((view, index) => {
    lines.push("");
    lines.push(`${index + 1}. ${cleanValue(view.view_label, view.view_key)}`);
    lines.push(`   Качество кадра: ${formatPercent(view.quality_score)}`);
    lines.push(`   Результат: ${cleanValue(view.risk?.label)} (${cleanValue(view.risk?.score, 0)}%)`);
    if (Array.isArray(view.metric_cards) && view.metric_cards.length > 0) {
      metricLines(view.metric_cards).forEach((line) => lines.push(`   ${line}`));
    } else {
      lines.push("   Профильный ракурс: используется для контроля протокола.");
    }
  });
  return lines;
}

export function buildHumanReportText(result) {
  const report = result || {};
  const risk = report.risk || {};
  const lines = [
    "ScolioScan School",
    "Подробный отчёт скрининга осанки",
    "",
    "ОБЩАЯ ИНФОРМАЦИЯ",
    `Ученик: ${cleanValue(report.student_id)}`,
    `ID отчёта: ${cleanValue(report.report_id)}`,
    `Дата анализа: ${formatDate(report.timestamp)}`,
    `Режим: ${formatMode(report)}`,
    `Метод анализа: ${formatEngine(report.analysis_engine)}`,
    `Качество кадра: ${formatPercent(report.quality_score)}`,
    "",
    "ИТОГ",
    `Уровень риска: ${cleanValue(risk.label)}`,
    `Оценка риска: ${cleanValue(risk.score, 0)}%`,
    `Сработавшие признаки: ${cleanValue(risk.finding_count, 0)} из ${cleanValue(risk.total_metrics, 0)}`,
    `Краткий вывод: ${cleanValue(risk.headline, "результат сформирован")}`,
    humanConclusion(report),
    "",
    "МЕТРИКИ",
    ...metricLines(report.metric_cards),
    "",
    "РЕКОМЕНДАЦИИ",
    ...numberedList(report.recommendations),
    "",
    "ПЛАН ДЕЙСТВИЙ",
    ...carePlanLines(report.care_plan),
    ...viewLines(report.views),
    "",
    "Отчёт сформирован ScolioScan School."
  ];

  return lines.join("\n");
}

export function buildDownloadableReportText(result) {
  return `\ufeff${buildHumanReportText(result)}`;
}

export function reportTextFileName(result) {
  const reportId = cleanValue(result?.report_id, "screening-report");
  const safeId = reportId.replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^_+|_+$/g, "");
  return `${safeId || "screening-report"}-analysis.txt`;
}
