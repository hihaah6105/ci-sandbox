"""
model.py - One small client for the model endpoint the evaluator gives us.

The evaluator sets these environment variables (see the baseline harness):
  ANTHROPIC_BASE_URL + ANTHROPIC_API_KEY        -> Anthropic Messages API
  GOOGLE_GEMINI_BASE_URL + GEMINI_API_KEY       -> Gemini (via its OpenAI layer)
  MODEL_BASE_URL + MODEL_API_KEY                -> any OpenAI-compatible endpoint
  MODEL_NAME                                    -> the model id to call

Two wire formats cover everything we need: OpenAI chat-completions (free dev
models through OpenRouter or Groq, Gemini through its compatibility layer, and
the google-cloud route for non-Claude models) and Anthropic Messages (for the
paid cross-model gate). urllib only, so there is nothing to install.

Note on Opus 5.5: the sponsored route rejects temperature, top_p, top_k and a
forced tool choice, so none of those are ever sent.
"""
import json, os, time, urllib.error, urllib.request

TIMEOUT = int(os.environ.get("PLE_HTTP_TIMEOUT", "300"))
RETRIES = int(os.environ.get("PLE_HTTP_RETRIES", "5"))


class ModelError(RuntimeError):
    pass


def _post(url, headers, payload):
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read()[:600].decode("utf8", "replace")
            last = f"HTTP {e.code}: {detail}"
            # 429 and 5xx are worth waiting out; 4xx otherwise is our bug
            if e.code != 429 and e.code < 500:
                raise ModelError(last) from None
        except Exception as e:                       # network blip
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(2 ** attempt, 30))
    raise ModelError(f"gave up after {RETRIES} attempts: {last}")


def _tools_openai(tools):
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["schema"]}} for t in tools]


def _tools_anthropic(tools):
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["schema"]} for t in tools]


class Client:
    def __init__(self):
        self.model = os.environ.get("MODEL_NAME", "")
        if os.environ.get("ANTHROPIC_BASE_URL"):
            self.flavour = "anthropic"
            self.url = os.environ["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages"
            self.headers = {"content-type": "application/json",
                            "anthropic-version": "2023-06-01",
                            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", "")}
        else:
            self.flavour = "openai"
            base = (os.environ.get("MODEL_BASE_URL")
                    or os.environ.get("GOOGLE_GEMINI_BASE_URL", "")).rstrip("/")
            if not base:
                raise ModelError("no model endpoint in the environment "
                                 "(MODEL_BASE_URL / ANTHROPIC_BASE_URL / GOOGLE_GEMINI_BASE_URL)")
            if not base.endswith("/chat/completions"):
                base = base + ("" if base.endswith(("/v1", "/openai")) else "/v1") + "/chat/completions"
            self.url = base
            # Both keys can be present at once (the workflow passes an AI Studio
            # key and an Anthropic key), so pick by endpoint, never by priority.
            if "api.anthropic.com" in base:
                key = (os.environ.get("ANTHROPIC_API_KEY")
                       or os.environ.get("MODEL_API_KEY", ""))
            else:
                key = (os.environ.get("MODEL_API_KEY")
                       or os.environ.get("GEMINI_API_KEY", ""))
            self.headers = {"content-type": "application/json",
                            "authorization": f"Bearer {key}"}

    def chat(self, messages, tools, max_tokens=8192):
        """Returns {text, tool_calls:[{id,name,args}], usage:{in,out}}."""
        if self.flavour == "anthropic":
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            conv = [{"role": m["role"], "content": m["content"]}
                    for m in messages if m["role"] != "system"]
            data = _post(self.url, self.headers,
                         {"model": self.model, "max_tokens": max_tokens,
                          "system": system, "messages": conv,
                          "tools": _tools_anthropic(tools)})
            text, calls = "", []
            for block in data.get("content") or []:
                if block.get("type") == "text":
                    text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    calls.append({"id": block.get("id", ""), "name": block.get("name", ""),
                                  "args": block.get("input") or {}})
            u = data.get("usage") or {}
            return {"text": text, "tool_calls": calls,
                    "meta": {"finish_reason": data.get("stop_reason")},
                    "usage": {"in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                              "out": u.get("output_tokens", 0),
                              "cache_read": u.get("cache_read_input_tokens", 0)}}

        data = _post(self.url, self.headers,
                     {"model": self.model, "max_tokens": max_tokens,
                      "messages": messages, "tools": _tools_openai(tools)})
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        meta = {"finish_reason": choice.get("finish_reason"),
                "message_keys": sorted(msg.keys()),
                "n_choices": len(data.get("choices") or []),
                "top_keys": sorted(data.keys())}
        calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {"_raw": fn.get("arguments")}
            calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "args": args})
        u = data.get("usage") or {}
        return {"text": msg.get("content") or "", "tool_calls": calls, "meta": meta,
                "usage": {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                          "cache_read": ((u.get("prompt_tokens_details") or {})
                                         .get("cached_tokens", 0))}}


TOOLS = [
    {"name": "bash", "description": "Run a bash command in the task container.",
     "schema": {"type": "object", "properties": {
         "command": {"type": "string", "description": "the command to run"}},
         "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file from the task container.",
     "schema": {"type": "object", "properties": {
         "path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write a text file in the task container.",
     "schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
    {"name": "write_document", "description":
        "Write the actual deliverable. Give the whole document as markdown and the "
        "harness builds the real .docx, .xlsx, .pdf, .md or .txt at that path. Use "
        "this instead of scripting python-docx or openpyxl. Markdown pipe tables "
        "become real worksheet rows in an .xlsx, one sheet per table. Write the "
        "complete document, not a fragment - every call replaces the whole file.",
     "schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "the deliverable path from your contract"},
         "markdown": {"type": "string", "description": "the entire document"}},
         "required": ["path", "markdown"]}},
    {"name": "http", "description":
        "Call one of the task's app servers (issue tracker, wiki, mail, file store) "
        "listed under app server in your working state. Use it to read project data "
        "that is not in the files, and to make changes the brief asks for, such as "
        "raising a tracker item - app state is part of the graded deliverable.",
     "schema": {"type": "object", "properties": {
         "url": {"type": "string"}, "method": {"type": "string"},
         "body": {"type": "string"}, "content_type": {"type": "string"}},
         "required": ["url"]}},
    {"name": "record", "description":
        "Record evidence, a constraint, a decision or a check in the working state. "
        "Constraints are frozen once recorded: to change a design, change a decision, "
        "never a constraint.",
     "schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["fact", "constraint", "decision", "check", "note"]},
         "name": {"type": "string"}, "value": {"type": "string"},
         "unit": {"type": "string"}, "source": {"type": "string"},
         "hard": {"type": "boolean"}, "slack": {"type": "string"}},
         "required": ["kind", "name"]}},
    {"name": "done", "description":
        "Finish. Only call this after the conformance gate passes.",
     "schema": {"type": "object", "properties": {
         "summary": {"type": "string"}}, "required": ["summary"]}},
]
