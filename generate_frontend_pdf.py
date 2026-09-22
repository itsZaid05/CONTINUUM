import os
import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(50, 752, "CONTINUUM: Frontend Integration Contract & Specification | Samsung PRISM Theme 05")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.75)
            self.line(50, 744, 562, 744)
            
        # Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(562, 34, page_text)
        self.drawString(50, 34, "PRISM GenAI Hackathon 2026-27 | Release Tag: PRISM_GENAI_HACKATHON_Y2026 | Role: Frontend UI")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.75)
        self.line(50, 46, 562, 46)
        
        self.restoreState()

def build_frontend_pdf(filename="CONTINUUM_Frontend_Integration_Contract.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=50,
        rightMargin=50,
        topMargin=46,
        bottomMargin=46
    )
    
    styles = getSampleStyleSheet()
    
    primary_color = colors.HexColor("#0F2942")     # Samsung Dark Navy
    secondary_color = colors.HexColor("#0284C7")   # Precision Cyan/Blue
    text_dark = colors.HexColor("#0F172A")
    code_bg = colors.HexColor("#F8FAFC")
    tag_bg = colors.HexColor("#F1F5F9")
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=primary_color,
        spaceAfter=2
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.2,
        leading=10.5,
        textColor=secondary_color,
        spaceAfter=4
    )
    
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11.5,
        textColor=primary_color,
        spaceBefore=5,
        spaceAfter=2,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7,
        leading=9.2,
        textColor=text_dark,
        spaceAfter=2
    )
    
    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=6,
        leading=7.8,
        textColor=colors.HexColor("#0F172A")
    )
    
    table_text_style = ParagraphStyle(
        'TableText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=6.5,
        leading=8.2,
        textColor=text_dark
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=6.5,
        leading=8.2,
        textColor=colors.white
    )

    story = []
    
    # -------------------------------------------------------------
    # PAGE 1: TITLE & CORE PROTOCOLS
    # -------------------------------------------------------------
    story.append(Paragraph("CONTINUUM: Frontend Integration Contract & Specification", title_style))
    story.append(Paragraph("PRISM Generative AI Hackathon (3rd Edition 2026-27) | Theme 05: Interruptible Real-Time Agents", subtitle_style))
    
    # Header Info Table
    header_data = [
        [
            Paragraph("<b>Target Role:</b> Frontend Engineer (React + Tailwind)", table_text_style),
            Paragraph("<b>WebSocket URI:</b> ws://localhost:8000/ws/{session_id}", table_text_style),
            Paragraph("<b>REST Base:</b> http://localhost:8000/api/v1", table_text_style)
        ],
        [
            Paragraph("<b>Evaluation Scope:</b> Split-Screen HUD, DAG Visualizer (30%)", table_text_style),
            Paragraph("<b>Session ID:</b> Frontend generated (<font name='Courier'>sess_&lt;id&gt;</font>)", table_text_style),
            Paragraph("<b>Instant ACK Budget:</b> &lt; 10ms", table_text_style)
        ]
    ]
    t_header = Table(header_data, colWidths=[180, 180, 152])
    t_header.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), tag_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_header)
    story.append(Spacer(1, 3))
    
    # 1. Session Lifecycle & User Input
    story.append(Paragraph("1. Session Lifecycle, Ownership & Inbound Protocol", h1_style))
    story.append(Paragraph(
        "• <b>Session ID Ownership:</b> <u>Frontend generates</u> <font name='Courier'>session_id</font> using format <font name='Courier'>sess_&lt;alphanumeric&gt;</font> (e.g. <font name='Courier'>`sess_${crypto.randomUUID().slice(0,8)}`</font> or <font name='Courier'>sess_demo</font>).<br/>"
        "• <b>Connection & Handshake:</b> Frontend opens <font name='Courier'>ws://localhost:8000/ws/{session_id}</font>. Backend immediately responds with <font name='Courier'>SESSION_INIT</font> containing the current DAG.<br/>"
        "• <b>Reconnection Protocol:</b> If connection drops, frontend attempts reconnect with backoff (1s, 2s, 4s). On reconnect to the same <font name='Courier'>session_id</font>, backend re-sends <font name='Courier'>SESSION_INIT</font> to restore state.<br/>"
        "• <b>User Inbound Utterance / Interruption:</b> Send via WebSocket (preferred) or REST <font name='Courier'>POST /api/v1/session/interrupt</font>.",
        body_style
    ))
    
    # JSON examples table
    input_data = [
        [
            Paragraph("<b>WebSocket Inbound Message:</b>", table_header_style),
            Paragraph("<b>REST Inbound Request & Instant Response (&lt;10ms):</b>", table_header_style)
        ],
        [
            Paragraph(
                "<font color='#0F766E'>// Client -> WS</font><br/>"
                "{\n  \"action\": \"utterance\",\n  \"session_id\": \"sess_89f2c1\",\n  \"text\": \"Actually, make it Bangalore, keep morning\"\n}",
                code_style
            ),
            Paragraph(
                "<font color='#0F766E'>// POST /api/v1/session/interrupt</font><br/>"
                "{\n  \"session_id\": \"sess_89f2c1\",\n  \"utterance\": \"Actually, make it Bangalore, keep morning\"\n}<br/>"
                "<font color='#0F766E'>// Immediate Response (&lt;10ms)</font><br/>"
                "{\n  \"event_id\": \"evt_9a41\", \"version\": 2, \"ack_emitted\": true, \"latency_ms\": 3.6\n}",
                code_style
            )
        ]
    ]
    t_input = Table(input_data, colWidths=[256, 256])
    t_input.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('BACKGROUND', (0,1), (-1,-1), code_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_input)
    story.append(Spacer(1, 3))
    
    # 2. Unified WebSocket Envelope
    story.append(Paragraph("2. Unified WebSocket Event Protocol & Envelope", h1_style))
    story.append(Paragraph(
        "Every event streamed from the backend adheres strictly to the following frozen envelope structure:",
        body_style
    ))
    
    envelope_code = (
        "type CONTINUUMEventType = 'SESSION_INIT' | 'ACK' | 'INTENT_UPDATE' | 'VERSION_UPDATE' | 'NODE_UPDATE' | 'METRICS_UPDATE' | 'CLARIFICATION_REQUEST' | 'FINAL_RESPONSE' | 'ERROR';\n\n"
        "interface CONTINUUMWebSocketEvent {\n"
        "  event_type: CONTINUUMEventType;\n"
        "  session_id: string;        // e.g. 'sess_89f2c1'\n"
        "  event_id: string;          // e.g. 'evt_9a41c2'\n"
        "  version: number;           // Monotonic version counter ($V_1, V_2, V_3$)\n"
        "  timestamp: number;         // Unix epoch in milliseconds\n"
        "  step_id?: string;          // 'p_1', 'p_2', 'sh_1' (where applicable)\n"
        "  branch_id?: string;        // 'PRIMARY' | 'SHADOW_1' | 'SHADOW_2'\n"
        "  status?: string;           // Lifecycle status\n"
        "  payload: Record&lt;string, any&gt;; // Complete event-specific schema\n"
        "}"
    )
    t_env = Table([[Paragraph(envelope_code.replace("\n", "<br/>").replace(" ", "&nbsp;"), code_style)]], colWidths=[512])
    t_env.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), code_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_env)
    story.append(Spacer(1, 3))

    # 3. Exhaustive WebSocket Events Table
    story.append(Paragraph("3. Exhaustive WebSocket Event Payloads (All Event Types Defined)", h1_style))
    events_table_data = [
        [
            Paragraph("Event Type", table_header_style),
            Paragraph("Trigger & Timing", table_header_style),
            Paragraph("Exact JSON Payload Structure", table_header_style)
        ],
        [
            Paragraph("<b>SESSION_INIT</b>", table_text_style),
            Paragraph("On WebSocket Connect (0ms)", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"SESSION_INIT\", \"payload\": {\"current_version\": 0, \"active_tasks_count\": 0, \"dag\": {\"session_id\": \"sess_89f\", \"nodes\": {}}}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>ACK</b>", table_text_style),
            Paragraph("Instant Fast Path (&lt;10ms)", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"ACK\", \"version\": 2, \"payload\": {\"text\": \"Got it, updating...\", \"latency_ms\": 3.6}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>INTENT_UPDATE</b>", table_text_style),
            Paragraph("5-Way Arbiter + IVS (&lt;250ms)", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"INTENT_UPDATE\", \"payload\": {\"delta_type\": \"MODIFY\", \"confidence\": 0.94, \"ivs_score\": 0.35, \"authorization\": \"IMPLIED\", \"affected_fields\": [\"destination\"], \"preserved_constraints\": [\"departure_time: morning\"]}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>VERSION_UPDATE</b>", table_text_style),
            Paragraph("On Interrupt Arrival (&lt;5ms)", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"VERSION_UPDATE\", \"payload\": {\"previous_version\": 1, \"current_version\": 2}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>NODE_UPDATE</b>", table_text_style),
            Paragraph("Lifecycle State Change (Full Node State)", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"NODE_UPDATE\", \"step_id\": \"p_1\", \"status\": \"RUNNING\", \"payload\": {\"step_id\": \"p_1\", \"tool\": \"search_flights\", \"risk\": \"FREE\", \"params\": {\"to\": \"Bangalore\"}, \"depends_on\": [], \"status\": \"RUNNING\", \"base_version\": 2, \"is_shadow\": false, \"branch_id\": \"PRIMARY\", \"output\": null, \"duration_ms\": null}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>CLARIFICATION_REQ</b>", table_text_style),
            Paragraph("When IVS &gt; 0.60 / Conf &lt; 0.70", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"CLARIFICATION_REQUEST\", \"payload\": {\"prompt\": \"Unsure about destination. Search Bangalore or hold?\", \"suggested_actions\": [\"Search Bangalore\", \"Hold\"]}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>METRICS_UPDATE</b>", table_text_style),
            Paragraph("HUD real-time telemetry", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"METRICS_UPDATE\", \"payload\": {\"pivot_latency_ms\": 3.8, \"median_pivot_latency_ms\": 4.1, \"p95_pivot_latency_ms\": 7.8, \"ivs_score\": 0.30, \"confidence\": 0.94, \"token_savings_pct\": 72.0, \"speculation_hits\": 4, \"speculation_wasted\": 1, \"branch_cleanup_time_ms\": 0.0, \"unnecessary_clarification_rate_pct\": 0.0, \"regretted_actions\": 0, \"active_tasks_count\": 1}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>FINAL_RESPONSE</b>", table_text_style),
            Paragraph("Workflow Completion", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"FINAL_RESPONSE\", \"status\": \"SUCCESS\", \"payload\": {\"summary\": \"Found morning flight to Bangalore and held seat.\", \"booking_ref\": null, \"hold_id\": \"HLD_9a8\", \"amount_paid\": 0, \"completed_steps\": [\"p_1\", \"p_2\"], \"status\": \"SUCCESS\"}}</font>", table_text_style)
        ],
        [
            Paragraph("<b>ERROR</b>", table_text_style),
            Paragraph("Policy Blocked / Execution Failure", table_text_style),
            Paragraph("<font name='Courier'>{\"event_type\": \"ERROR\", \"status\": \"ERROR\", \"payload\": {\"error_code\": \"POLICY_BLOCKED\", \"message\": \"Autonomous booking blocked: IRREVERSIBLE requires EXPLICIT auth & IVS < 0.60.\", \"step_id\": \"p_3\", \"recoverable\": true, \"suggested_action\": \"Confirm explicitly\"}}</font>", table_text_style)
        ]
    ]
    t_events = Table(events_table_data, colWidths=[90, 110, 312])
    t_events.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 1.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
        ('LEFTPADDING', (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_events)
    
    # -------------------------------------------------------------
    # PAGE 2: DAG DATA MODEL, HUD, SPLIT SCREEN & MOCK SEQUENCE
    # -------------------------------------------------------------
    story.append(PageBreak())
    
    story.append(Paragraph("4. DAG Data Model & Node Lifecycle UI Specification (All 8 States)", h1_style))
    story.append(Paragraph(
        "Every <font name='Courier'>NODE_UPDATE</font> contains the complete node state matching <font name='Courier'>DAGNodeModel</font> so frontend never needs to guess missing properties:",
        body_style
    ))
    
    dag_model_code = (
        "interface DAGNodeModel {\n"
        "  step_id: string;        // Unique node ID ('p_1', 'p_2', 'sh_1')\n"
        "  tool: string;           // 'search_flights' | 'hold_seat' | 'confirm_booking' | 'search_cabs' | 'search_hotels'\n"
        "  params: Record&lt;string, any&gt;; // e.g. { to: 'Bangalore', slot: 'morning' }\n"
        "  risk: 'FREE' | 'STAGEABLE' | 'MUTATING' | 'IRREVERSIBLE';\n"
        "  depends_on: string[];   // Parent dependency step_ids, e.g. ['p_1']\n"
        "  status: 'CREATED' | 'RUNNING' | 'COMPLETED' | 'INVALIDATED' | 'CANCELLED' | 'SHADOW' | 'PROMOTED' | 'ABANDONED';\n"
        "  base_version: number;   // Originating version\n"
        "  is_shadow: boolean;     // Speculative branch flag\n"
        "  branch_id: string;      // 'PRIMARY' | 'SHADOW_1'\n"
        "  output?: any;           // Result payload when COMPLETED\n"
        "  error?: string;         // Cancellation/Invalidation reason\n"
        "  duration_ms?: number;   // Execution duration\n"
        "}"
    )
    t_dag = Table([[Paragraph(dag_model_code.replace("\n", "<br/>").replace(" ", "&nbsp;"), code_style)]], colWidths=[512])
    t_dag.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), code_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_dag)
    story.append(Spacer(1, 3))
    
    # Tailwind Node Status Guidelines (All 8 States)
    dag_styles_data = [
        [Paragraph("Node Status", table_header_style), Paragraph("Tailwind / CSS Styling Guideline", table_header_style), Paragraph("Lifecycle Meaning & UI Behavior", table_header_style)],
        [Paragraph("<b>CREATED</b>", table_text_style), Paragraph("<font name='Courier'>border-slate-600 bg-slate-900/40 text-slate-300</font>", table_text_style), Paragraph("Queued in DAG, waiting for parent dependencies", table_text_style)],
        [Paragraph("<b>RUNNING</b>", table_text_style), Paragraph("<font name='Courier'>border-blue-500 bg-blue-950/20 text-blue-300 animate-pulse</font>", table_text_style), Paragraph("Task active in asyncio task registry", table_text_style)],
        [Paragraph("<b>COMPLETED</b>", table_text_style), Paragraph("<font name='Courier'>border-emerald-500 bg-emerald-950/20 text-emerald-300</font>", table_text_style), Paragraph("Task successfully executed & verified", table_text_style)],
        [Paragraph("<b>INVALIDATED</b>", table_text_style), Paragraph("<font name='Courier'>border-rose-500 line-through opacity-60 text-rose-300</font>", table_text_style), Paragraph("Surgically pruned by DAG dependency tracker on parameter change", table_text_style)],
        [Paragraph("<b>CANCELLED</b>", table_text_style), Paragraph("<font name='Courier'>border-rose-500 line-through bg-rose-950/20 text-rose-300</font>", table_text_style), Paragraph("Aborted immediately via task.cancel() on retraction", table_text_style)],
        [Paragraph("<b>SHADOW</b>", table_text_style), Paragraph("<font name='Courier'>border-purple-500 border-dashed bg-purple-950/20 text-purple-300</font>", table_text_style), Paragraph("Read-only speculative background pre-fetch", table_text_style)],
        [Paragraph("<b>PROMOTED</b>", table_text_style), Paragraph("<font name='Courier'>border-amber-400 bg-amber-950/20 text-amber-300 ring-1 ring-amber-400</font>", table_text_style), Paragraph("Speculative shadow branch adopted into active primary plan", table_text_style)],
        [Paragraph("<b>ABANDONED</b>", table_text_style), Paragraph("<font name='Courier'>border-slate-700 opacity-40 text-slate-500</font>", table_text_style), Paragraph("Stale Gate discarded result ($V_{task} != V_{curr}$)", table_text_style)]
    ]
    t_dag_styles = Table(dag_styles_data, colWidths=[70, 240, 202])
    t_dag_styles.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 1.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
        ('LEFTPADDING', (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_dag_styles)
    story.append(Spacer(1, 3))

    # 5. Split-Screen Comparison
    story.append(Paragraph("5. Split-Screen Comparison: Baseline vs CONTINUUM", h1_style))
    split_data = [
        [Paragraph("Component", table_header_style), Paragraph("Left Panel: Vanilla Baseline Agent", table_header_style), Paragraph("Right Panel: CONTINUUM Engine", table_header_style)],
        [Paragraph("<b>Interrupt Handling</b>", table_text_style), Paragraph("Discards all progress; full sequential re-prompt", table_text_style), Paragraph("Instant ACK (&lt;10ms), surgical DAG reuse", table_text_style)],
        [Paragraph("<b>Tokens Consumed</b>", table_text_style), Paragraph("Accumulates <b>1,250 tokens</b> per turn", table_text_style), Paragraph("Uses <b>350 tokens</b> per turn (<b>72% Savings</b>)", table_text_style)],
        [Paragraph("<b>Pivot Latency</b>", table_text_style), Paragraph("High latency (<b>~420ms</b> LLM roundtrip)", table_text_style), Paragraph("Sub-millisecond local dispatch (<b>~3.6ms</b>)", table_text_style)],
        [Paragraph("<b>Safety Gating</b>", table_text_style), Paragraph("No Policy Engine; risks committing old actions", table_text_style), Paragraph("Stale Gate + Two-Phase Effect Ledger (0 Regrets)", table_text_style)]
    ]
    t_split = Table(split_data, colWidths=[90, 211, 211])
    t_split.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), primary_color),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 1.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
        ('LEFTPADDING', (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_split)
    story.append(Spacer(1, 3))

    # 6. Mock Sequence for Demo
    story.append(Paragraph("6. Mock WebSocket Event Sequence for Frontend Development", h1_style))
    story.append(Paragraph(
        "Frontend developers can use this standalone sequence to test the complete live UI without running the backend:",
        body_style
    ))
    
    mock_seq = (
        "// 1. Initial Prompt: 'Find flights to Delhi tomorrow morning'\n"
        "WS.emit({ event_type: 'ACK', version: 1, payload: { text: 'Got it, updating...', latency_ms: 3.4 } });\n"
        "WS.emit({ event_type: 'INTENT_UPDATE', version: 1, payload: { delta_type: 'NEW_GOAL', ivs_score: 0.15 } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'p_1', status: 'RUNNING', payload: { step_id: 'p_1', tool: 'search_flights', risk: 'FREE', params: { to: 'Delhi', slot: 'morning' }, depends_on: [], status: 'RUNNING', base_version: 1, is_shadow: false, branch_id: 'PRIMARY' } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'sh_1', status: 'SHADOW', payload: { step_id: 'sh_1', tool: 'search_cabs', risk: 'FREE', hypothesis: 'Delhi airport transit', params: { to: 'Delhi Airport' }, depends_on: [], status: 'SHADOW', base_version: 1, is_shadow: true, branch_id: 'SHADOW_1' } });\n\n"
        "// 2. User Interrupt at t=1200ms: 'Actually, make it Bangalore, keep morning'\n"
        "WS.emit({ event_type: 'ACK', version: 2, payload: { text: 'Got it, updating...', latency_ms: 3.8 } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'p_1', status: 'CANCELLED', payload: { step_id: 'p_1', tool: 'search_flights', risk: 'FREE', params: { to: 'Delhi' }, depends_on: [], status: 'CANCELLED', base_version: 2, is_shadow: false, branch_id: 'PRIMARY', error: 'Surgically invalidated by pivot' } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'sh_1', status: 'ABANDONED', payload: { step_id: 'sh_1', tool: 'search_cabs', risk: 'FREE', params: { to: 'Delhi Airport' }, depends_on: [], status: 'ABANDONED', base_version: 1, is_shadow: true, branch_id: 'SHADOW_1', error: 'Stale Gate: Speculative Delhi cab discarded' } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'p_1_blr', status: 'RUNNING', payload: { step_id: 'p_1_blr', tool: 'search_flights', risk: 'FREE', params: { to: 'Bangalore', slot: 'morning' }, depends_on: [], status: 'RUNNING', base_version: 2, is_shadow: false, branch_id: 'PRIMARY' } });\n"
        "WS.emit({ event_type: 'NODE_UPDATE', step_id: 'p_1_blr', status: 'COMPLETED', payload: { step_id: 'p_1_blr', tool: 'search_flights', risk: 'FREE', params: { to: 'Bangalore', slot: 'morning' }, depends_on: [], status: 'COMPLETED', base_version: 2, is_shadow: false, branch_id: 'PRIMARY', output: { flight_id: 'FL_BLR_702', price: 5400 } } });\n"
        "WS.emit({ event_type: 'METRICS_UPDATE', version: 2, payload: { pivot_latency_ms: 3.8, median_pivot_latency_ms: 4.1, p95_pivot_latency_ms: 7.8, ivs_score: 0.30, confidence: 0.94, token_savings_pct: 72.0, speculation_hits: 4, speculation_wasted: 1, branch_cleanup_time_ms: 0.0, unnecessary_clarification_rate_pct: 0.0, regretted_actions: 0, active_tasks_count: 1 } });"
    )
    t_mock = Table([[Paragraph(mock_seq.replace("\n", "<br/>").replace(" ", "&nbsp;"), code_style)]], colWidths=[512])
    t_mock.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), code_bg),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_mock)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Generated PDF successfully: {filename}")

if __name__ == "__main__":
    build_frontend_pdf()
