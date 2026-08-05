import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


OPERATOR_HTML = Path(__file__).parents[1] / "static" / "operador.html"


class _ElementOrderParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.append(element_id)


def _operator_script():
    html = OPERATOR_HTML.read_text(encoding="utf-8")
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match, "operador.html precisa conter o script de controle"
    return match.group(1)


def _recognition_state(version, state="reconhecimento"):
    return {
        "estado": state,
        "id_prova": 1,
        "id_reconhecimento": 7,
        "duracao_segundos": 420,
        "intervalo_segundos": 180,
        "reconhecimento_restante": 419.0 if state == "reconhecimento" else None,
        "intervalo_restante": 179.0 if state == "intervalo" else None,
        "versao": version,
        "iniciado_em": "2026-08-04T12:00:00+00:00",
        "reconhecimento_finalizado_em": None,
        "cancelado_em": None,
    }


def _run_operator_script(scenario, *, action_state=None):
    action_state = action_state or _recognition_state(2)
    harness = f"""
const elements = new Map();
function makeElement(id = '') {{
  const classes = new Set();
  let classNameValue = '';
  const syncClassName = () => {{ classNameValue = [...classes].join(' '); }};
  const element = {{
    id,
    value: id === 'recognitionMinutes' ? '7' : '',
    checked: false,
    disabled: false,
    innerHTML: '',
    textContent: '',
    style: {{}},
    appendChild() {{}},
    classList: {{
      add(...names) {{ names.forEach(name => classes.add(name)); syncClassName(); }},
      remove(...names) {{ names.forEach(name => classes.delete(name)); syncClassName(); }},
      toggle(name, force) {{
        const enabled = force === undefined ? !classes.has(name) : Boolean(force);
        if (enabled) classes.add(name); else classes.delete(name);
        syncClassName();
        return enabled;
      }},
      contains(name) {{ return classes.has(name); }},
    }},
  }};
  Object.defineProperty(element, 'className', {{
    get() {{ return classNameValue; }},
    set(value) {{
      classNameValue = String(value);
      classes.clear();
      classNameValue.split(/\\s+/).filter(Boolean).forEach(name => classes.add(name));
    }},
  }});
  return element;
}}

globalThis.document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  }},
  createElement() {{ return makeElement(); }},
}};
globalThis.performance = {{ now: () => 1000 }};
globalThis.requestAnimationFrame = () => 0;
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => undefined;
globalThis.location = {{ protocol: 'http:', host: 'localhost:8000' }};
globalThis.alert = () => undefined;
globalThis.confirm = () => true;

const sockets = [];
globalThis.WebSocket = class {{
  constructor(url) {{ this.url = url; sockets.push(this); }}
  close() {{}}
}};

const actionState = {json.dumps(action_state)};
function response(data) {{
  return {{
    ok: true,
    status: 200,
    async json() {{ return data; }},
    clone() {{ return response(data); }},
  }};
}}
globalThis.fetch = async url => {{
  if (url.startsWith('/inscricoes/') || url.startsWith('/provas/')) return response([]);
  if (url.startsWith('/reconhecimento-pista/')) return response(actionState);
  return response({{}});
}};
"""
    completed = subprocess.run(
        ["node"],
        input=f"{harness}\n{_operator_script()}\n{scenario}",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_queued_initial_websocket_snapshot_cannot_overwrite_newer_http_action():
    stale_socket_state = json.dumps(_recognition_state(1))
    result = _run_operator_script(
        f"""
(async () => {{
  await recognitionAction('/reconhecimento-pista/iniciar', {{id_prova: 1, duracao_segundos: 420}});
  const recognitionSocket = sockets.find(socket => socket.url.endsWith('/ws/reconhecimento-pista'));
  recognitionSocket.onmessage({{
    data: JSON.stringify({{tipo: 'estado_reconhecimento', data: {stale_socket_state}}}),
  }});
  console.log(JSON.stringify({{version: recognitionSnapshot.versao, state: recognitionSnapshot.estado}}));
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    )

    assert result == {"version": 2, "state": "reconhecimento"}


def test_first_snapshot_of_a_reconnection_can_recover_after_backend_restart():
    previous_backend_state = json.dumps(_recognition_state(10, "liberado"))
    restarted_backend_state = json.dumps(_recognition_state(1, "intervalo"))
    result = _run_operator_script(
        f"""
setRecognitionSnapshot({previous_backend_state});
recognitionWs = null;
connectRecognitionWebSocket();
const reconnectedSocket = sockets[sockets.length - 1];
reconnectedSocket.onmessage({{
  data: JSON.stringify({{tipo: 'estado_reconhecimento', data: {restarted_backend_state}}}),
}});
console.log(JSON.stringify({{version: recognitionSnapshot.versao, state: recognitionSnapshot.estado}}));
"""
    )

    assert result == {"version": 1, "state": "intervalo"}


def test_recognition_precedes_and_visually_demotes_proof_timers_while_active():
    parser = _ElementOrderParser()
    parser.feed(OPERATOR_HTML.read_text(encoding="utf-8"))

    assert parser.ids.index("recognitionSection") < parser.ids.index("proofTimers")

    active_state = json.dumps(_recognition_state(3, "intervalo"))
    waiting_state = json.dumps(
        {
            **_recognition_state(4, "aguardando"),
            "id_prova": None,
            "id_reconhecimento": None,
        }
    )
    result = _run_operator_script(
        f"""
applyRecognitionState({active_state});
const active = {{
  recognitionEmphasized: elements.get('recognitionSection').classList.contains('is-active'),
  proofTimersDemoted: elements.get('proofTimers').classList.contains('recognition-secondary'),
}};
applyRecognitionState({waiting_state});
console.log(JSON.stringify({{
  active,
  waiting: {{
    recognitionEmphasized: elements.get('recognitionSection').classList.contains('is-active'),
    proofTimersDemoted: elements.get('proofTimers').classList.contains('recognition-secondary'),
  }},
}}));
"""
    )

    assert result == {
        "active": {"recognitionEmphasized": True, "proofTimersDemoted": True},
        "waiting": {"recognitionEmphasized": False, "proofTimersDemoted": False},
    }


def test_official_run_states_disable_recognition_start_without_inferring_history():
    waiting_state = json.dumps(
        {
            **_recognition_state(1, "aguardando"),
            "id_prova": None,
            "id_reconhecimento": None,
        }
    )
    result = _run_operator_script(
        f"""
const proofState = (estado, versao) => ({{
  estado,
  versao,
  id_prova: 1,
  faltas: 0,
  recusas: 0,
  tia_decorrido: 0,
  top_decorrido: 0,
}});
setRecognitionSnapshot({waiting_state});
setSnapshot(proofState('idle', 1));
const idle = elements.get('btnStartRecognition').disabled;
const active = {{}};
for (const [index, estado] of ['autorizado', 'rodando', 'finalizado'].entries()) {{
  setSnapshot(proofState(estado, index + 2));
  active[estado] = elements.get('btnStartRecognition').disabled;
}}
setSnapshot(proofState('idle', 5));
console.log(JSON.stringify({{
  idle,
  active,
  idleAfterCurrentRun: elements.get('btnStartRecognition').disabled,
}}));
"""
    )

    assert result == {
        "idle": False,
        "active": {"autorizado": True, "rodando": True, "finalizado": True},
        "idleAfterCurrentRun": False,
    }


def test_recognition_action_displays_backend_conflict_detail():
    result = _run_operator_script(
        """
(async () => {
  const alerts = [];
  globalThis.alert = message => alerts.push(message);
  globalThis.fetch = async () => ({
    ok: false,
    status: 409,
    async json() {
      return {detail: 'Reconhecimento indisponível após a corrida oficial.'};
    },
  });
  const succeeded = await recognitionAction(
    '/reconhecimento-pista/iniciar',
    {id_prova: 1, duracao_segundos: 420},
  );
  console.log(JSON.stringify({succeeded, alerts}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    )

    assert result == {
        "succeeded": False,
        "alerts": [
            "Erro no reconhecimento: Reconhecimento indisponível após a corrida oficial."
        ],
    }
