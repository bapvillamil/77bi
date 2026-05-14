import json
import uuid
import base64
import tempfile
import os
import traceback
import subprocess
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services.intent_classifier import classify_intent
from .services.sql_engine import run_query, execute_builder_query
from .services.s3_service import load_all_data, get_file_schema
from .services.insight_engine import generate_insight, generate_insight_from_session

import io as _io
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage


def index(request):
    return render(request, 'dashboard/index.html')


# ── Schema endpoint ───────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def get_schema(request):
    """Returns column names and types from all CSV files in S3."""
    try:
        schemas, _ = load_all_data()
        fields = []
        seen = set()

        for schema in schemas:
            for col in schema.get('columns', []):
                if col not in seen:
                    seen.add(col)
                    samples = schema.get('unique_samples', {}).get(col, [])
                    field_type = _infer_field_type(col, samples)
                    fields.append({
                        'name':     col,
                        'type':     field_type,
                        'filename': schema.get('filename', ''),
                    })

        return JsonResponse({'fields': fields})

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


def _infer_field_type(col_name: str, samples: list) -> str:
    col_lower = col_name.lower()
    if any(kw in col_lower for kw in ['date', 'time', 'created', 'resolved', 'updated', 'due']):
        return 'date'
    if any(kw in col_lower for kw in ['count', 'total', 'amount', 'cost', 'days', 'hours',
                                       'points', 'score', 'number', 'num', 'qty', 'size']):
        return 'numeric'
    numeric_count = 0
    for val in samples:
        try:
            float(str(val).replace(',', ''))
            numeric_count += 1
        except (ValueError, TypeError):
            pass
    if numeric_count == len(samples) and len(samples) > 0:
        return 'numeric'
    return 'categorical'


# ── Dashboard (multi-widget) endpoints ───────────────────────────────────────

@require_http_methods(["GET"])
def get_dashboard(request):
    """Return all widgets currently in the dashboard session."""
    widgets = request.session.get('dashboard_widgets', [])
    return JsonResponse({'widgets': widgets})


@csrf_exempt
@require_http_methods(["POST"])
def add_widget(request):
    """Add a new widget to the dashboard."""
    try:
        body       = json.loads(request.body)
        chart_data = body.get('chart_data', {})
        title      = body.get('title', chart_data.get('result_label', 'Chart'))

        widget = {
            'id':         str(uuid.uuid4()),
            'title':      title,
            'chart_data': chart_data,
            'size':       body.get('size', 'medium'),  # small | medium | large | full
        }

        widgets = request.session.get('dashboard_widgets', [])
        widgets.append(widget)
        request.session['dashboard_widgets'] = widgets
        request.session.modified = True

        return JsonResponse({'widget': widget, 'total': len(widgets)})

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def remove_widget(request):
    """Remove a widget from the dashboard by id."""
    try:
        body      = json.loads(request.body)
        widget_id = body.get('id')

        widgets = request.session.get('dashboard_widgets', [])
        widgets = [w for w in widgets if w['id'] != widget_id]
        request.session['dashboard_widgets'] = widgets
        request.session.modified = True

        return JsonResponse({'status': 'removed', 'total': len(widgets)})

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def reorder_widgets(request):
    """Reorder widgets and optionally update titles (used for title rename too)."""
    try:
        body       = json.loads(request.body)
        order      = body.get('order', [])   # list of widget ids in new order
        titles     = body.get('titles', {})  # { widget_id: new_title } optional

        widgets    = request.session.get('dashboard_widgets', [])
        id_map     = {w['id']: w for w in widgets}

        # Apply title updates if provided
        for wid, new_title in titles.items():
            if wid in id_map and new_title:
                id_map[wid]['title'] = new_title

        reordered  = [id_map[wid] for wid in order if wid in id_map]

        request.session['dashboard_widgets'] = reordered
        request.session.modified = True

        return JsonResponse({'status': 'reordered'})

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


# ── Builder chart endpoint ────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def build_chart(request):
    """Executes a chart build from the drag-and-drop builder."""
    try:
        body        = json.loads(request.body)
        x_axis      = body.get('x_axis', '')
        y_axis      = body.get('y_axis', '')
        group_by    = body.get('group_by', '')
        chart_type  = body.get('chart_type', 'bar')
        filters     = body.get('filters', {})
        aggregation = body.get('aggregation', 'count')
        show_values = body.get('show_values', True)
        kpi_format  = body.get('kpi_format', 'number')

        # KPI only needs a value field; other types need x_axis
        if not x_axis and chart_type != 'kpi':
            return JsonResponse({'error': 'X axis field is required.'}, status=400)
        if chart_type == 'kpi' and not x_axis and not y_axis:
            return JsonResponse({'error': 'Please drop a field to use as the KPI value.'}, status=400)

        result = execute_builder_query(
            x_axis=x_axis,
            y_axis=y_axis,
            group_by=group_by,
            chart_type=chart_type,
            filters=filters,
            aggregation=aggregation,
            show_values=show_values,
            kpi_format=kpi_format,
        )

        if 'error' in result:
            return JsonResponse({'error': result['error']}, status=400)

        # Store as "last built" in session for insight
        request.session['chart_data'] = result
        request.session.modified = True

        return JsonResponse({
            'chart': {
                'type':         result['chart_type'],
                'labels':       result['labels'],
                'values':       result['values'],
                'x_axis_label': result['x_axis_label'],
                'y_axis_label': result['y_axis_label'],
                'result_label': result['result_label'],
                'datasets':     result.get('datasets'),
                'show_values':  result.get('show_values', True),
                'kpi_format':   result.get('kpi_format', 'number'),
            },
            'scope': result.get('scope', {}),
        })

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


# ── Insight endpoint (standalone) ────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def get_insight(request):
    """Generate insight for a specific widget's data."""
    try:
        body       = json.loads(request.body)
        user_query = body.get('query', '').strip()
        chart_data = body.get('chart_data', {})

        if not user_query:
            return JsonResponse({'error': 'Query is required.'}, status=400)

        if not chart_data:
            chart_data = request.session.get('chart_data', {})

        if not chart_data:
            return JsonResponse({'error': 'No chart data to analyze.'}, status=400)

        chat_history = request.session.get('chat_history', [])
        insight = generate_insight(
            user_query=user_query,
            chart_data=chart_data,
            chat_history=chat_history,
        )

        _append_chat_history(request, user_query, insight)
        return JsonResponse({'insight': insight})

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


# ── Main NL query endpoint ────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def process_query(request):
    """Natural language query — routes to Intent 1 / 2 / 3."""
    try:
        body       = json.loads(request.body)
        user_query = body.get('query', '').strip()

        if not user_query:
            return JsonResponse({'error': 'Query is required.'}, status=400)

        session_context = {
            'chart_data':   request.session.get('chart_data', {}),
            'chat_history': request.session.get('chat_history', []),
        }

        classification = classify_intent(user_query, session_context)
        intent         = classification['intent']
        response_data  = {'intent': intent}
        print("Intent classification:", intent)

        if intent == 'intent_1':
            chart_result = run_query(user_query)
            if 'error' in chart_result:
                return JsonResponse({'error': chart_result['error']}, status=400)

            request.session['chart_data'] = chart_result
            request.session.modified      = True

            response_data['chart'] = {
                'type':         chart_result['chart_type'],
                'labels':       chart_result['labels'],
                'values':       chart_result['values'],
                'x_axis_label': chart_result['x_axis_label'],
                'y_axis_label': chart_result['y_axis_label'],
                'result_label': chart_result['result_label'],
            }
            response_data['scope']      = chart_result.get('scope', {})
            response_data['chart_data'] = chart_result

        elif intent == 'intent_2':
            insight = generate_insight_from_session(
                user_query=user_query,
                session_context=session_context,
            )
            response_data['insight'] = insight
            _append_chat_history(request, user_query, insight)

        elif intent == 'intent_3':
            chart_result = run_query(user_query)
            if 'error' in chart_result:
                return JsonResponse({'error': chart_result['error']}, status=400)

            request.session['chart_data'] = chart_result
            request.session.modified      = True

            insight = generate_insight(
                user_query=user_query,
                chart_data=chart_result,
                chat_history=session_context.get('chat_history', []),
            )

            response_data['chart'] = {
                'type':         chart_result['chart_type'],
                'labels':       chart_result['labels'],
                'values':       chart_result['values'],
                'x_axis_label': chart_result['x_axis_label'],
                'y_axis_label': chart_result['y_axis_label'],
                'result_label': chart_result['result_label'],
            }
            response_data['insight']    = insight
            response_data['scope']      = chart_result.get('scope', {})
            response_data['chart_data'] = chart_result
            _append_chat_history(request, user_query, insight)

        request.session.modified = True
        return JsonResponse(response_data)

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def clear_session(request):
    request.session.flush()
    return JsonResponse({'status': 'session cleared'})


def _append_chat_history(request, user_query: str, insight: str):
    history = request.session.get('chat_history', [])
    history.append({'role': 'user',      'content': user_query})
    history.append({'role': 'assistant', 'content': insight})
    request.session['chat_history'] = history[-20:]


# ── Dashboard View page ───────────────────────────────────────────────────────

def dashboard_view(request):
    """Render the full-page dashboard view with export options."""
    widgets      = request.session.get('dashboard_widgets', [])
    chat_history = request.session.get('chat_history', [])

    # Get the latest assistant insight
    latest_insight = ''
    for turn in reversed(chat_history):
        if turn.get('role') == 'assistant':
            latest_insight = turn.get('content', '')
            break

    # Pre-encode each widget's chart_data as a safe JSON string for the template.
    # Using double-quote JSON inside a double-quoted HTML attribute (escaped) avoids
    # the single-quote breakage that occurs with |safe on Python dicts.
    widgets_with_json = []
    for w in widgets:
        widgets_with_json.append({
            'id':             w.get('id', ''),
            'title':          w.get('title', 'Chart'),
            'size':           w.get('size', 'medium'),
            'chart_data_json': json.dumps(w.get('chart_data', {})),
        })

    return render(request, 'dashboard/dashboard_view.html', {
        'widgets':      widgets_with_json,
        'insight_text': latest_insight,
    })


@csrf_exempt
@require_http_methods(["POST"])
def export_pptx(request):
    """
    Receive chart images (base64 PNG) + insight text,
    build a .pptx via a Node.js pptxgenjs script, return binary.
    """
    try:
        body       = json.loads(request.body)
        widgets    = body.get('widgets', [])
        dash_title = body.get('title', '77BI Dashboard')

        # ── Locate pptxgenjs ──────────────────────────────────────────────────
        # Find where `node` lives, then look for pptxgenjs relative to it.
        # Covers: global npm install, nvm, and local project node_modules.
        def find_pptxgenjs():
            """Return an absolute path to pptxgenjs/dist/pptxgen.cjs, or None."""
            import shutil

            candidates = []

            # 1. Project-local node_modules (most reliable on Windows)
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates.append(
                os.path.join(project_root, 'node_modules', 'pptxgenjs')
            )

            # 2. Sibling of the node executable (global npm prefix)
            node_bin = shutil.which('node')
            if node_bin:
                # node lives at <prefix>/bin/node or <prefix>\node.exe
                node_dir = os.path.dirname(node_bin)
                # On Windows: node.exe sits in the npm prefix root
                # On Unix:    node sits in <prefix>/bin/
                for base in [node_dir, os.path.dirname(node_dir)]:
                    candidates.append(
                        os.path.join(base, 'node_modules', 'pptxgenjs')
                    )
                    candidates.append(
                        os.path.join(base, 'lib', 'node_modules', 'pptxgenjs')
                    )

            # 3. Common global npm roots
            home = os.path.expanduser('~')
            candidates += [
                os.path.join(home, 'AppData', 'Roaming', 'npm', 'node_modules', 'pptxgenjs'),
                '/usr/local/lib/node_modules/pptxgenjs',
                '/usr/lib/node_modules/pptxgenjs',
            ]

            for p in candidates:
                if os.path.isdir(p):
                    return p
            return None

        pptxgenjs_dir = find_pptxgenjs()
        if not pptxgenjs_dir:
            raise RuntimeError(
                "pptxgenjs not found. Run: npm install -g pptxgenjs  "
                "(or npm install pptxgenjs inside your project directory)"
            )

        # Use forward slashes — Node.js require() handles them on all platforms
        pptxgenjs_require = pptxgenjs_dir.replace('\\', '/')

        # ── Helper: safe JS string ────────────────────────────────────────────
        def js_str(s):
            if not s:
                return ''
            # Drop non-BMP (emoji etc.) — pptxgenjs renders them as boxes anyway
            s = ''.join(c if ord(c) <= 0xFFFF else '?' for c in s)
            s = s.replace('\\', '\\\\')
            s = s.replace("'", "\\'")
            s = s.replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n')
            # Remove remaining control characters
            s = ''.join(c if ord(c) >= 0x20 else ' ' for c in s)
            return s

        # ── Build per-slide JS ────────────────────────────────────────────────
        slides_js = ''
        for idx, w in enumerate(widgets):
            title   = js_str(w.get('title', 'Chart'))
            img_b64 = w.get('chart_image_b64', '')
            insight = js_str(w.get('insight_text', '') or 'No insight generated.')

            data_uri = ''
            if img_b64:
                data_uri = img_b64 if img_b64.startswith('data:') else f'data:image/png;base64,{img_b64}'

            img_line = (
                f"slide.addImage({{ data: '{data_uri}', x: 0.15, y: 0.7, w: 7.0, h: 4.7 }});"
                if data_uri else '// no chart image'
            )

            slides_js += f"""
// ── Slide {idx + 1}: {title} ──────────────────────────
{{
  let slide = pres.addSlide();
  slide.background = {{ color: 'FFFFFF' }};

  // Title bar
  slide.addShape(pres.ShapeType.rect, {{
    x: 0, y: 0, w: 10, h: 0.55,
    fill: {{ color: '1D4ED8' }}, line: {{ color: '1D4ED8' }}
  }});
  slide.addText('{title}', {{
    x: 0.2, y: 0, w: 9.6, h: 0.55,
    fontSize: 16, bold: true, color: 'FFFFFF', valign: 'middle', margin: 0
  }});

  // Chart area (72%)
  slide.addShape(pres.ShapeType.rect, {{
    x: 0, y: 0.55, w: 7.2, h: 5.075,
    fill: {{ color: 'FFFFFF' }}, line: {{ color: 'E5E7EB', width: 0.5 }}
  }});

  // Insight panel (28%)
  slide.addShape(pres.ShapeType.rect, {{
    x: 7.2, y: 0.55, w: 2.8, h: 5.075,
    fill: {{ color: 'F9FAFB' }}, line: {{ color: 'E5E7EB', width: 0.5 }}
  }});
  slide.addText('AI INSIGHT', {{
    x: 7.25, y: 0.62, w: 2.65, h: 0.28,
    fontSize: 8, bold: true, color: '1D4ED8', margin: 0
  }});
  slide.addText('{insight}', {{
    x: 7.25, y: 0.95, w: 2.65, h: 4.55,
    fontSize: 7.5, color: '374151', valign: 'top', wrap: true, margin: 0
  }});

  // Chart image
  {img_line}
}}
"""

        dash_js = js_str(dash_title)

        node_script = f"""
'use strict';
// Load pptxgenjs from its resolved absolute path
const pptxgen = require('{pptxgenjs_require}');
let pres = new pptxgen();
pres.layout  = 'LAYOUT_16x9';
pres.title   = '{dash_js}';
pres.author  = '77BI';

// ── Cover slide ───────────────────────────────────────────
{{
  let cover = pres.addSlide();
  cover.background = {{ color: '1D4ED8' }};
  cover.addText('{dash_js}', {{
    x: 0.5, y: 1.8, w: 9, h: 1.2,
    fontSize: 32, bold: true, color: 'FFFFFF', align: 'center'
  }});
  cover.addText('Generated by 77BI  |  AI-Powered BI', {{
    x: 0.5, y: 3.2, w: 9, h: 0.5,
    fontSize: 14, color: 'BFDBFE', align: 'center'
  }});
  cover.addText(
    new Date().toLocaleDateString('en-US', {{ year: 'numeric', month: 'long', day: 'numeric' }}),
    {{ x: 0.5, y: 4.5, w: 9, h: 0.5, fontSize: 11, color: 'BFDBFE', align: 'center' }}
  );
}}

{slides_js}

pres.writeFile({{ fileName: process.argv[2] }})
  .then(() => process.exit(0))
  .catch(e => {{ console.error(e.message || e); process.exit(1); }});
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, 'gen.js')
            output_path = os.path.join(tmpdir, 'dashboard.pptx')

            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(node_script)

            result = subprocess.run(
                ['node', script_path, output_path],
                capture_output=True, text=True,
                timeout=60, encoding='utf-8',
            )

            if result.returncode != 0:
                raise RuntimeError(f'pptxgenjs error: {result.stderr}')

            with open(output_path, 'rb') as f:
                pptx_bytes = f.read()

        response = HttpResponse(
            pptx_bytes,
            content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
        )
        response['Content-Disposition'] = 'attachment; filename="77BI_dashboard.pptx"'
        return response

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)
    
@csrf_exempt
@require_http_methods(["POST"])
def export_pdf(request):
    """
    Receive chart images (base64 PNG) + insight text from the frontend.
    Renders one widget per landscape-A4 page by drawing directly onto a
    ReportLab canvas — no Table flowables, so no LayoutError possible.
    """
    try:

        body       = json.loads(request.body)
        widgets    = body.get('widgets', [])
        dash_title = body.get('title', '77BI Dashboard')

        # ── Page geometry (all in points) ─────────────────────────────────────
        PAGE_W, PAGE_H = landscape(A4)   # 841.89 x 595.28 pt
        M  = 18   # margin pt
        TH = 26   # title bar height pt
        GW = 8    # gutter width pt

        # Usable rect after margins
        UX = M
        UY = M
        UW = PAGE_W - 2 * M
        UH = PAGE_H - 2 * M

        # Column split: 72% chart / 28% insight
        CHART_W   = UW * 0.72
        INSIGHT_W = UW * 0.28

        # Image area inside chart column (below title bar, inside padding)
        PAD       = 8
        IMG_X     = UX + PAD
        IMG_Y     = UY + PAD                        # bottom-left in PDF coords
        IMG_MAX_W = CHART_W - 2 * PAD
        IMG_MAX_H = UH - TH - 2 * PAD

        # Insight text area
        INS_X  = UX + CHART_W + GW + PAD
        INS_W  = INSIGHT_W - GW - 2 * PAD

        def rl_safe(text):
            return ''.join(
                c if ord(c) < 128 else '?'
                for c in (text or '')
            )

        def fit_image(px_w, px_h, max_w, max_h):
            """
            Convert html2canvas (scale=2) pixel dims to pt, then shrink-to-fit.
            html2canvas scale=2: 2 raw px per CSS px; 1 CSS px = 0.75 pt
            """
            nat_w = (px_w / 2.0) * 0.75
            nat_h = (px_h / 2.0) * 0.75
            scale = min(max_w / nat_w, max_h / nat_h)
            # scale can be > 1 for small charts — cap at 1 (don't upscale)
            scale = min(scale, 1.0)
            return nat_w * scale, nat_h * scale

        buf = _io.BytesIO()
        c   = rl_canvas.Canvas(buf, pagesize=landscape(A4))

        # ── Helper: draw one page ─────────────────────────────────────────────
        def draw_page(title, img_data, insight_text, is_first=False):
            # Background
            c.setFillColor(colors.HexColor('#F3F4F6'))
            c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

            # White card background
            c.setFillColor(colors.white)
            c.roundRect(UX, UY, UW, UH, radius=6, fill=1, stroke=0)

            # Blue title bar
            c.setFillColor(colors.HexColor('#1D4ED8'))
            c.roundRect(UX, UY + UH - TH, UW, TH, radius=6, fill=1, stroke=0)
            # Cover bottom-radius of title bar (so it looks flat-bottom)
            c.rect(UX, UY + UH - TH, UW, TH / 2, fill=1, stroke=0)

            # Title text
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 11)
            c.drawString(UX + 10, UY + UH - TH + 8, rl_safe(title))

            # Chart / insight divider line
            div_x = UX + CHART_W
            c.setStrokeColor(colors.HexColor('#E5E7EB'))
            c.setLineWidth(0.5)
            c.line(div_x, UY, div_x, UY + UH - TH)

            # Insight panel background
            c.setFillColor(colors.HexColor('#F9FAFB'))
            c.rect(div_x, UY, INSIGHT_W, UH - TH, fill=1, stroke=0)

            # Outer border
            c.setStrokeColor(colors.HexColor('#E5E7EB'))
            c.setLineWidth(0.5)
            c.roundRect(UX, UY, UW, UH, radius=6, fill=0, stroke=1)

            # ── Chart image ───────────────────────────────────────────────────
            if img_data:
                pil_img    = PILImage.open(_io.BytesIO(img_data))
                px_w, px_h = pil_img.size
                iw, ih     = fit_image(px_w, px_h, IMG_MAX_W, IMG_MAX_H)

                # Centre the image vertically in the chart area
                area_h   = UH - TH - 2 * PAD
                img_y    = UY + PAD + (area_h - ih) / 2   # centred

                img_reader = ImageReader(_io.BytesIO(img_data))
                c.drawImage(
                    img_reader,
                    IMG_X, img_y, width=iw, height=ih,
                    preserveAspectRatio=True, mask='auto',
                )

            # ── Insight text (manual word-wrap) ───────────────────────────────
            ins_text = rl_safe(insight_text or 'No insight generated.')

            # "AI INSIGHT" label
            c.setFillColor(colors.HexColor('#1D4ED8'))
            c.setFont('Helvetica-Bold', 7.5)
            label_y = UY + UH - TH - PAD - 10
            c.drawString(INS_X, label_y, 'AI INSIGHT')

            # Body text
            c.setFillColor(colors.HexColor('#374151'))
            c.setFont('Helvetica', 7.5)
            line_h    = 11           # pt between lines
            max_lines = int((label_y - UY - PAD - line_h) / line_h)
            text_y    = label_y - line_h - 4

            # Simple word-wrap
            words     = ins_text.split()
            line_buf  = ''
            lines_drawn = 0

            for word in words:
                test = (line_buf + ' ' + word).strip()
                if c.stringWidth(test, 'Helvetica', 7.5) <= INS_W:
                    line_buf = test
                else:
                    if lines_drawn < max_lines:
                        c.drawString(INS_X, text_y - lines_drawn * line_h, line_buf)
                        lines_drawn += 1
                    line_buf = word

            # Last line
            if line_buf and lines_drawn < max_lines:
                c.drawString(INS_X, text_y - lines_drawn * line_h, line_buf)

        # ── Cover page ────────────────────────────────────────────────────────
        c.setFillColor(colors.HexColor('#1D4ED8'))
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 28)
        c.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 20, rl_safe(dash_title))

        c.setFillColor(colors.HexColor('#BFDBFE'))
        c.setFont('Helvetica', 13)
        c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 20, 'Generated by 77BI  |  AI-Powered BI')

        from datetime import date
        c.setFont('Helvetica', 10)
        c.drawCentredString(PAGE_W / 2, PAGE_H / 2 - 44,
                            date.today().strftime('%B %d, %Y'))
        c.showPage()

        # ── One page per widget ───────────────────────────────────────────────
        for w in widgets:
            title   = w.get('title', 'Chart')
            img_b64 = w.get('chart_image_b64', '')
            insight = w.get('insight_text', '') or 'No insight generated.'

            img_data = None
            if img_b64:
                raw      = img_b64.split(',')[-1]
                img_data = base64.b64decode(raw)

            draw_page(title, img_data, insight)
            c.showPage()

        c.save()
        buf.seek(0)

        response = HttpResponse(buf.read(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="77BI_dashboard.pdf"'
        return response

    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)