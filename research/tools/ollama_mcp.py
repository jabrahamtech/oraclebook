"""Minimal MCP server (stdio, JSON-RPC 2.0) for an Ollama instance, e.g. on a home NAS.
Standard library only, so it runs under the system python3.

Tools: ollama_discover (scan a /24 for Ollama on :11434), ollama_list_models, ollama_running,
ollama_show, ollama_chat. The server talks only to OLLAMA_URL (or a host found by discover and
passed explicitly as `url`); it never runs commands or touches files.

Register (once), then restart Claude Code:
  claude mcp add ollama-nas -s user -e OLLAMA_URL=http://<nas-ip>:11434 -- \
      /usr/bin/python3 /path/to/research/tools/ollama_mcp.py
OLLAMA_URL may be omitted; ollama_discover then finds candidates on the local network.
"""

import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROTOCOL = "2025-06-18"
DEFAULT_URL = os.environ.get("OLLAMA_URL", "").rstrip("/")


# A NAS on the LAN is reached directly, never through an HTTP(S)_PROXY from the environment.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(url, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def base(args):
    url = (args.get("url") or DEFAULT_URL).rstrip("/")
    if not url:
        raise ValueError("no Ollama URL: set OLLAMA_URL or pass `url` (see ollama_discover)")
    return url


def local_subnets():
    """The /24 of each local IPv4 address (best effort, no external calls)."""
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET address: picks the default interface, sends nothing
        nets.add(ipaddress.ip_network(s.getsockname()[0] + "/24", strict=False))
        s.close()
    except OSError:
        pass
    return sorted(str(n) for n in nets)


def discover(args):
    nets = [args["subnet"]] if args.get("subnet") else local_subnets()
    port = int(args.get("port", 11434))
    hosts = [str(h) for n in nets for h in ipaddress.ip_network(n, strict=False).hosts()]

    def probe(h):
        try:
            with socket.create_connection((h, port), timeout=0.6):
                pass
            d = http(f"http://{h}:{port}/api/tags", timeout=3)
            return {"url": f"http://{h}:{port}", "models": [m["name"] for m in d.get("models", [])]}
        except (OSError, ValueError, urllib.error.URLError):
            return None

    with ThreadPoolExecutor(max_workers=64) as ex:
        found = [r for r in ex.map(probe, hosts) if r]
    return {"subnets": nets, "found": found}


def list_models(args):
    d = http(base(args) + "/api/tags", timeout=15)
    return [
        {
            "name": m["name"],
            "size_gb": round(m.get("size", 0) / 1e9, 2),
            "parameters": m.get("details", {}).get("parameter_size"),
            "quantization": m.get("details", {}).get("quantization_level"),
            "family": m.get("details", {}).get("family"),
        }
        for m in d.get("models", [])
    ]


def running(args):
    return http(base(args) + "/api/ps", timeout=15)


def show(args):
    d = http(base(args) + "/api/show", {"model": args["model"]}, timeout=30)
    return {k: d.get(k) for k in ("details", "parameters", "template", "model_info") if k in d}


def chat(args):
    msgs = []
    if args.get("system"):
        msgs.append({"role": "system", "content": args["system"]})
    msgs.append({"role": "user", "content": args["prompt"]})
    body = {
        "model": args["model"],
        "messages": msgs,
        "stream": False,
        "options": {"temperature": args.get("temperature", 0), **(args.get("options") or {})},
    }
    if args.get("format"):
        body["format"] = args["format"]
    d = http(base(args) + "/api/chat", body, timeout=int(args.get("timeout", 600)))
    return {
        "content": d.get("message", {}).get("content"),
        "eval_count": d.get("eval_count"),
        "total_duration_s": round((d.get("total_duration") or 0) / 1e9, 2),
    }


URL_PROP = {"url": {"type": "string", "description": "Ollama base URL; default OLLAMA_URL"}}
TOOLS = {
    "ollama_discover": (
        discover,
        "Scan the local /24 (or `subnet`) for Ollama servers on port 11434 and list their models.",
        {
            "subnet": {"type": "string", "description": "e.g. 192.168.20.0/24"},
            "port": {"type": "integer"},
        },
        [],
    ),
    "ollama_list_models": (list_models, "Models pulled on the Ollama server.", URL_PROP, []),
    "ollama_running": (running, "Models currently loaded in memory (/api/ps).", URL_PROP, []),
    "ollama_show": (
        show,
        "Details of one model (parameters, template, context length).",
        {**URL_PROP, "model": {"type": "string"}},
        ["model"],
    ),
    "ollama_chat": (
        chat,
        "One chat completion (non-streaming). Temperature defaults to 0.",
        {
            **URL_PROP,
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "system": {"type": "string"},
            "temperature": {"type": "number"},
            "format": {"description": "'json' or a JSON schema for structured output"},
            "options": {"type": "object", "description": "extra Ollama options (num_ctx, ...)"},
            "timeout": {"type": "integer", "description": "seconds, default 600"},
        },
        ["model", "prompt"],
    ),
}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, id_ = msg.get("method"), msg.get("id")
    if id_ is None:
        return  # notification
    if method == "initialize":
        reply(
            id_,
            {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ollama-nas", "version": "0.1.0"},
            },
        )
    elif method == "ping":
        reply(id_, {})
    elif method == "tools/list":
        reply(
            id_,
            {
                "tools": [
                    {
                        "name": n,
                        "description": d,
                        "inputSchema": {"type": "object", "properties": p, "required": r},
                    }
                    for n, (_, d, p, r) in TOOLS.items()
                ]
            },
        )
    elif method == "tools/call":
        p = msg.get("params", {})
        tool = TOOLS.get(p.get("name"))
        if tool is None:
            reply(id_, error={"code": -32602, "message": f"unknown tool {p.get('name')}"})
            return
        try:
            out = tool[0](p.get("arguments") or {})
            reply(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]})
        except Exception as e:  # noqa: BLE001 — report tool failures to the client, keep serving
            reply(
                id_,
                {
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                    "isError": True,
                },
            )
    else:
        reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                handle(json.loads(line))
            except json.JSONDecodeError:
                reply(None, error={"code": -32700, "message": "parse error"})


if __name__ == "__main__":
    main()
