import json
import subprocess
import textwrap
import unittest


class ReportExportTests(unittest.TestCase):
    def test_human_report_export_is_readable_text(self):
        sample_result = {
            "report_id": "DEMO-001_20260709_120000",
            "student_id": "DEMO-001",
            "timestamp": "2026-07-09T12:00:00+00:00",
            "mode": "single",
            "landmarks_found": True,
            "analysis_engine": "mediapipe_tasks_lite",
            "quality_score": 0.93,
            "risk": {
                "level": "moderate",
                "label": "Средний риск",
                "headline": "Есть признаки асимметрии",
                "score": 40,
                "finding_count": 2,
                "total_metrics": 5,
            },
            "metric_cards": [
                {
                    "key": "shoulder_tilt",
                    "title": "Наклон плеч",
                    "description": "разница высоты плечевых ориентиров",
                    "value": 4.2,
                    "threshold": 3.0,
                    "unit": "deg",
                    "triggered": True,
                }
            ],
            "recommendations": ["Повторить снимок для подтверждения результата."],
            "care_plan": [
                {
                    "title": "Очный школьный осмотр",
                    "body": "Проверить результат у школьного медработника.",
                    "level": "attention",
                }
            ],
        }
        script = textwrap.dedent(
            f"""
            import {{ buildHumanReportText, reportTextFileName }} from './app/reportExport.mjs';
            const result = {json.dumps(sample_result, ensure_ascii=False)};
            console.log('FILE=' + reportTextFileName(result));
            console.log(buildHumanReportText(result));
            """
        )

        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        output = completed.stdout

        self.assertIn("FILE=DEMO-001_20260709_120000-analysis.txt", output)
        self.assertIn("Подробный отчёт скрининга осанки", output)
        self.assertIn("ИТОГ", output)
        self.assertIn("МЕТРИКИ", output)
        self.assertIn("Наклон плеч: 4.2°", output)
        self.assertIn("РЕКОМЕНДАЦИИ", output)
        self.assertIn("ПЛАН ДЕЙСТВИЙ", output)
        self.assertNotIn('"metric_cards"', output)


if __name__ == "__main__":
    unittest.main()
