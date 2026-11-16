"""Cloud Notes: a small Flask app for the Observability lab.

It sends traces, metrics and logs with the Microsoft OpenTelemetry Distro:
- to Application Insights when APPLICATIONINSIGHTS_CONNECTION_STRING is set,
- to any OTLP/HTTP endpoint (e.g. an OpenTelemetry Collector) when
  OTEL_EXPORTER_OTLP_ENDPOINT is set,
- to both when both are set. With neither, the app runs and sends nothing.
"""
import logging
import os
import random
import time

from microsoft.opentelemetry import use_microsoft_opentelemetry

# Configure OpenTelemetry before Flask is imported, so that Flask, requests
# and the "notes" logger are instrumented automatically.
to_azure = bool(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))
to_otlp = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
if to_azure or to_otlp:
    use_microsoft_opentelemetry(enable_azure_monitor=to_azure, logger_name="notes")

import requests  # noqa: E402
from flask import Flask, redirect, render_template_string, request  # noqa: E402
from opentelemetry import metrics, trace  # noqa: E402

log = logging.getLogger("notes")
log.setLevel(logging.INFO)
tracer = trace.get_tracer("notes.web")
meter = metrics.get_meter("notes.web")
notes_created = meter.create_counter(
    "app.notes.created", unit="{note}", description="Number of notes created")

app = Flask(__name__)

# Kept in memory only: a restart loses the notes (see the Storage lab).
NOTES = ["Buy oat milk", "Read the SRE book chapter on monitoring"]

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cloud Notes</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }
    li { margin: .3rem 0; }
    input[type=text] { width: 70%; }
  </style>
</head>
<body>
  <h1>Cloud Notes</h1>
  <form method="post" action="/notes">
    <input type="text" name="text" required placeholder="New note">
    <button>Add</button>
  </form>
  <p>The newest {{ notes | length }} of {{ total }} notes:</p>
  <ul>{% for note in notes %}<li>{{ note }}</li>{% endfor %}</ul>
  <p>
    <a href="/slow">Recommendations (slow)</a> ·
    <a href="/fail">Archive (broken)</a> ·
    <a href="/healthz">Health</a>
  </p>
</body>
</html>"""


@app.get("/")
def index():
    newest = NOTES[::-1][:15]
    return render_template_string(PAGE, notes=newest, total=len(NOTES))


@app.post("/notes")
def create_note():
    text = request.form.get("text", "").strip()
    if text:
        # A manual span with a business attribute, and a custom metric.
        with tracer.start_as_current_span("save_note") as span:
            span.set_attribute("app.note.length", len(text))
            NOTES.append(text)
            notes_created.add(1)
        log.info("Note created", extra={"note_length": len(text)})
    return redirect("/")


@app.get("/slow")
def recommendations():
    # An outgoing HTTP call: a dependency on the application map.
    try:
        requests.get("https://example.com/", timeout=10)
    except requests.RequestException:
        log.warning("example.com is not reachable")
    # Pretend that a slow database query runs here.
    with tracer.start_as_current_span("load_recommendations") as span:
        delay = random.uniform(0.8, 2.5)
        span.set_attribute("app.delay_seconds", round(delay, 2))
        time.sleep(delay)
    if delay > 2:
        log.warning("Recommendations took %.1f s", delay)
    return f"<p>Write fewer notes. (This page took {delay:.1f} s.)</p><p><a href='/'>Back</a></p>"


@app.get("/fail")
def archive():
    log.error("Cannot open the archive: connection pool exhausted")
    raise RuntimeError("database connection pool exhausted")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
