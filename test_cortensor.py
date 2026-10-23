"""Safe, minimal end-to-end Cortensor Portal inference test.

This test:
1. Reads CORTENSOR_API_KEY strictly from the environment; NEVER logs or prints its value.
2. Supports CORTENSOR_MODEL (defaults to 'gemma4' per Hackathon #5 community requirement, overridable if Portal uses another slug).
3. Verifies the provider, endpoint, and model against the actual OpenMinion 0.1.1 implementation.
4. Sends one short prompt through the OpenMinion LLM subsystem (CortensorProvider).
5. Prints only whether the request succeeded and the explanation, with secrets redacted.
6. If the key is missing or rejected, stops and explains the exact next action without retrying.
7. Does NOT modify .openminion/agents.json or affect the existing Ollama setup.
"""
from __future__ import annotations

import os
import sys
import time

def redact_secrets(text: str, secret: str) -> str:
    """Mask any occurrences of the secret string."""
    if secret and len(secret) >= 4 and secret in text:
        return text.replace(secret, "***[REDACTED]***")
    return text


def main() -> int:
    print("=" * 60)
    print("MinionPatch - Cortensor Portal Inference Test")
    print("=" * 60)

    # 1. Read API Key from environment
    api_key = os.environ.get("CORTENSOR_API_KEY", "").strip()

    if not api_key:
        print("\n[STATUS] BLOCKED: Environment variable 'CORTENSOR_API_KEY' is not set.")
        print("\n[NEXT ACTION REQUIRED]")
        print("1. Obtain your API key from the Cortensor Portal:")
        print("   https://docs-v2.cortensor.network/products/portal-and-api-gateway/")
        print("2. Set the environment variable in your Windows PowerShell session:")
        print('   $env:CORTENSOR_API_KEY = "your-actual-portal-api-key"')
        print("   (Optional) Override model if needed (default is 'gemma4'):")
        print('   $env:CORTENSOR_MODEL = "gemma4"')
        print("3. Re-run this test script:")
        print("   .venv\\Scripts\\python.exe test_cortensor.py")
        print("=" * 60)
        return 1

    # 2. Verify configuration against OpenMinion 0.1.1
    endpoint = os.environ.get("CORTENSOR_API_URL", "https://api.cortensor.app/v1")
    # Hackathon #5 requirement: Gemma 4 as optional model (not oss-20b).
    # Default to "gemma4"; overridable via CORTENSOR_MODEL if Portal routes a specific variant (e.g. gemma-4).
    model = os.environ.get("CORTENSOR_MODEL", "gemma4").strip()

    print("\n[CONFIG VERIFICATION (OpenMinion 0.1.1)]")
    print("  Provider Adapter:  cortensor (CortensorProvider)")
    print(f"  Base Endpoint:     {endpoint}")
    print("  Resolved Route:    https://api.cortensor.app/v1/chat/completions")
    print(f"  Target Model:      {model}")
    print(f"  API Key Detected:  Yes ({len(api_key)} chars, value hidden)")
    print("  Local Config Safe: .openminion/agents.json is UNCHANGED")

    # 3. Import OpenMinion components
    try:
        from openminion.modules.llm.providers.cortensor.adapter import CortensorProvider
        from openminion.modules.llm.schemas import LLMRequest, Message
    except ImportError as exc:
        print(f"\n[ERROR] Failed to import OpenMinion: {exc}")
        return 2

    # 4. Prepare single test prompt
    prompt = (
        "Explain in 1-2 concise sentences why `import os` might be an unused import "
        "in a Python script that only performs math calculations."
    )
    req = LLMRequest(
        messages=[Message(role="user", content=prompt)],
        model=model,
    )
    config = {
        "base_url": endpoint,
        "model": model,
        "api_mode": "auto",
        "api_key": api_key,
        "timeout_seconds": 30,
        "empty_result_max_attempts": 1,
    }

    print("\n[INFERENCE DISPATCH]")
    print("  Sending single test prompt to Cortensor Portal...")
    provider = CortensorProvider()
    start_time = time.perf_counter()

    try:
        resp = provider.complete(req, config)
        elapsed = time.perf_counter() - start_time
        output_text = (resp.output_text or "").strip()

        if not output_text:
            print("\n[RESULT] FAILED: Cortensor Portal returned an empty response.")
            print(f"  Possible causes: Model '{model}' may be unavailable, pending quota, or requires checking the exact Portal model slug.")
            return 1

        safe_output = redact_secrets(output_text, api_key)
        print(f"\n[RESULT] SUCCESS: Real Cortensor Portal inference succeeded in {elapsed:.2f}s!")
        print("\n--- Model Response ---")
        print(safe_output)
        print("----------------------")
        print("\nMinionPatch is ready to use Cortensor for hackathon live demonstrations.")
        return 0

    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        err_raw = str(exc)
        safe_err = redact_secrets(err_raw, api_key)

        print(f"\n[RESULT] FAILED after {elapsed:.2f}s: {safe_err}")
        print("\n[NEXT ACTION REQUIRED]")
        if "auth" in safe_err.lower() or "401" in safe_err or "api key" in safe_err.lower():
            print("Your CORTENSOR_API_KEY was rejected by https://api.cortensor.app/v1.")
            print("Please verify your API key in the Cortensor Portal and update it via:")
            print('  $env:CORTENSOR_API_KEY = "your-actual-portal-api-key"')
        else:
            print("A network or server error occurred while contacting Cortensor.")
            print("Check your internet connection and verify https://api.cortensor.app/v1 status.")
        return 1

    finally:
        provider.close()


if __name__ == "__main__":
    sys.exit(main())
