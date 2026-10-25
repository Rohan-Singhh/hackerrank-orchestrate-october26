"""Minimal model client with disk cache and usage ledger (no SDK dependency).

Providers (auto-detected from the environment, first match wins):
  OPENROUTER_API_KEY -> OpenAI-compatible chat completions at openrouter.ai
  OPENAI_API_KEY     -> OpenAI chat completions
  ANTHROPIC_API_KEY  -> Anthropic Messages API

Every call is cached under code/llm_cache/<sha256>.json so a later run with no
key (or --offline) reproduces the same answers with zero network calls.  The
ledger (code/llm_cache/usage_ledger.jsonl) records model, tokens and latency
per live call; evaluation/usage_report.md is generated from it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
DEFAULT_CONFIG = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "text_model": "google/gemini-2.0-flash-exp:free", "vision_model": "google/gemini-2.0-flash-exp:free",
                   "prices_per_mtok": {"default": [0.0, 0.0]}},
    "openai": {"base_url": "https://api.openai.com/v1", "text_model": "gpt-4o-mini", "vision_model": "gpt-4o-mini",
               "prices_per_mtok": {"gpt-4o-mini": [0.15, 0.60]}},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "text_model": "claude-haiku-4-5", "vision_model": "claude-sonnet-4-5",
                  "prices_per_mtok": {"claude-haiku-4-5": [1.0, 5.0], "claude-sonnet-4-5": [3.0, 15.0]}},
    "concurrency": 4,
    "max_retries": 5,
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            user = json.load(fh)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


class ModelUnavailable(RuntimeError):
    pass


class Client:
    def __init__(self, provider: Optional[str], api_key: Optional[str], cfg: dict, cache_dir: Path, offline: bool = False, refresh: bool = False):
        self.provider = provider
        self.api_key = api_key
        self.cfg = cfg
        self.pcfg = cfg.get(provider, {}) if provider else {}
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.refresh = refresh
        self.ledger_path = cache_dir / "usage_ledger.jsonl"
        self.stats = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0, "cache_misses_offline": 0}

    # ---- public --------------------------------------------------------
    @property
    def live(self) -> bool:
        return bool(self.provider and self.api_key and not self.offline)

    def text_model(self) -> str:
        return self.pcfg.get("text_model", "")

    def vision_model(self) -> str:
        return self.pcfg.get("vision_model", "")

    def complete(self, prompt: str, system: str = "", images: Optional[List[Path]] = None, model: Optional[str] = None,
                 max_tokens: int = 800, purpose: str = "") -> Optional[str]:
        """Return the model text, from cache when available; None when unavailable offline."""
        images = images or []
        model = model or (self.vision_model() if images else self.text_model())
        key_src = json.dumps({"p": self.provider, "m": model, "s": system, "u": prompt, "i": [self._file_hash(i) for i in images]}, sort_keys=True)
        key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
        cpath = self.cache_dir / f"{key}.json"
        if cpath.exists() and not self.refresh:
            with open(cpath, encoding="utf-8") as fh:
                self.stats["cache_hits"] += 1
                return json.load(fh)["text"]
        if not self.live:
            self.stats["cache_misses_offline"] += 1
            return None
        text, usage = self._call(model, system, prompt, images, max_tokens)
        model = usage.get("model", model)
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump({"provider": self.provider, "model": model, "purpose": purpose, "text": text, "usage": usage, "prompt_sha256": key}, fh, ensure_ascii=False, indent=1)
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": self.provider, "model": model, "purpose": purpose,
                                 "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                                 "latency_ms": usage.get("latency_ms", 0), "prompt_sha256": key}) + "\n")
        return text

    def summary(self) -> dict:
        return dict(self.stats)

    # ---- internals -----------------------------------------------------
    @staticmethod
    def _file_hash(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            h.update(fh.read())
        return h.hexdigest()

    def _call(self, model: str, system: str, prompt: str, images: List[Path], max_tokens: int):
        chain = [model] + [m for m in self.pcfg.get("fallback_models", []) if m != model]
        last_exc: Optional[Exception] = None
        for m in chain:
            try:
                return self._call_one(m, system, prompt, images, max_tokens)
            except ModelUnavailable as exc:
                last_exc = exc
                if "HTTP 401" in str(exc):
                    raise
                continue
        raise ModelUnavailable(f"all models failed ({chain}): {last_exc}")

    def _call_one(self, model: str, system: str, prompt: str, images: List[Path], max_tokens: int):
        last_exc: Optional[Exception] = None
        for attempt in range(self.cfg.get("max_retries", 5)):
            t0 = time.time()
            try:
                if self.provider == "anthropic":
                    text, usage = self._anthropic(model, system, prompt, images, max_tokens)
                else:
                    text, usage = self._openai_compat(model, system, prompt, images, max_tokens)
                if not (text or "").strip():
                    raise urllib.error.URLError("empty completion")
                usage["latency_ms"] = int((time.time() - t0) * 1000)
                usage["model"] = model
                self.stats["calls"] += 1
                self.stats["input_tokens"] += usage.get("input_tokens", 0)
                self.stats["output_tokens"] += usage.get("output_tokens", 0)
                return text, usage
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:300]
                if exc.code == 401:
                    raise ModelUnavailable(f"HTTP 401 from {self.provider}: {body}\n  cause: the API key is missing or invalid\n  fix: export a valid key (or put it in an uncommitted .env) or run with --no-llm / --offline")
                if exc.code == 403:
                    raise ModelUnavailable(f"HTTP 403 from {self.provider} for {model}: {body}\n  cause: this model is not available to this key\n  fix: the next model in fallback_models is tried automatically")
                if exc.code == 404:
                    raise ModelUnavailable(f"HTTP 404 from {self.provider}: {body}\n  cause: model id {model!r} is not available on this provider\n  fix: set text_model/vision_model in code/config.json")
                last_exc = exc
                self.stats["errors"] += 1
                if exc.code == 429 and attempt >= 1:
                    break  # rate limited twice: move to the next model instead of waiting
                time.sleep(min(2 ** attempt, 8))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc
                self.stats["errors"] += 1
                time.sleep(min(2 ** attempt, 8))
        raise ModelUnavailable(f"model call failed after retries: {last_exc}")

    _ssl_ctx = None

    @classmethod
    def _ssl(cls):
        if cls._ssl_ctx is None:
            cafile = os.environ.get("SSL_CERT_FILE")
            if not cafile:
                try:
                    import certifi  # type: ignore
                    cafile = certifi.where()
                except ImportError:
                    cafile = None
            cls._ssl_ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
        return cls._ssl_ctx

    def _post(self, url: str, headers: dict, payload: dict, timeout: int = 75) -> dict:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=self._ssl()) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _openai_compat(self, model, system, prompt, images, max_tokens):
        content: List[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(img.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content if images else prompt})
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/interviewstreet/hackerrank-orchestrate-october26"
            headers["X-Title"] = "ghost-data-deletion-audit"
        data = self._post(f"{self.pcfg['base_url']}/chat/completions", headers, payload)
        if "choices" not in data:
            raise urllib.error.URLError(f"unexpected response: {str(data)[:200]}")
        text = data["choices"][0]["message"]["content"] or ""
        u = data.get("usage") or {}
        return text, {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}

    def _anthropic(self, model, system, prompt, images, max_tokens):
        content: List[dict] = []
        for img in images:
            mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(img.read_bytes()).decode("ascii")}})
        content.append({"type": "text", "text": prompt})
        payload = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": content}]}
        if system:
            payload["system"] = system
        data = self._post(f"{self.pcfg['base_url']}/messages", {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, payload)
        if "content" not in data:
            raise urllib.error.URLError(f"unexpected response: {str(data)[:200]}")
        text = "".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
        u = data.get("usage") or {}
        return text, {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}


KEY_VARS = (("OPENROUTER_API_KEY", "openrouter"), ("OPENAI_API_KEY", "openai"), ("ANTHROPIC_API_KEY", "anthropic"))
_DOTENV_KEYS: set = set()


def _load_dotenv() -> None:
    """Read KEY=value lines from an uncommitted .env (repo root or code/) into os.environ.

    Values already present in the environment win. Nothing is logged."""
    for p in (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ[k] = v  # an explicit .env wins over a value inherited from a long-running parent
                _DOTENV_KEYS.add(k)


def _load_windows_user_env() -> None:
    """On Windows, a key set with `setx` lives in the user registry and is not visible to
    processes started earlier; read it as a fallback."""
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            for env, _ in KEY_VARS:
                if env in _DOTENV_KEYS:
                    continue
                try:
                    v, _t = winreg.QueryValueEx(k, env)
                except OSError:
                    continue
                # the user-level value is the freshest one the person set; a stale copy
                # inherited by a long-running parent process must not shadow it
                if v and os.environ.get(env) != str(v):
                    os.environ[env] = str(v)
    except OSError:
        pass


def make_client(offline: bool = False, refresh: bool = False, cache_dir: Optional[Path] = None) -> Client:
    cfg = load_config()
    _load_dotenv()
    _load_windows_user_env()
    provider = api_key = None
    for env, name in KEY_VARS:
        if os.environ.get(env):
            provider, api_key = name, os.environ[env]
            break
    forced = os.environ.get("LLM_PROVIDER")
    if forced and os.environ.get({"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(forced, "")):
        provider = forced
        api_key = os.environ[{"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[forced]]
    cache_dir = cache_dir or (Path(__file__).resolve().parents[1] / "llm_cache")
    return Client(provider, api_key, cfg, cache_dir, offline=offline, refresh=refresh)


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (tolerates code fences)."""
    if text is None:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        j = s.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None
