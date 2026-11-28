# Provider API authentication

Codine supports OpenRouter browser authorization and manual provider API keys. This is API access billed by your provider; a ChatGPT or Claude subscription does not supply Codine API credits. Authentication never changes your session budget, shared ledger, provider selection or model selection.

Run these commands at your shell, after exiting an active Codine session:

```sh
codine auth status openrouter
codine auth login openrouter
codine auth set-key nvidia
codine auth logout openrouter
```

`auth` commands run before model configuration. `status` is an offline presence/source check, not proof that a key works or has credit. It prints no key, key hash or balance. `set-key` uses hidden terminal input; never put a secret in command arguments, model messages or a transcript. Browser login and key entry require an interactive terminal. They are not model tools or RPC authentication operations.

## Storage and precedence

Explicit login/key-entry saves credentials to `~/.config/codine/credentials/keys.json`. The directory is user-owned mode `0700` and records are mode `0600`, protected by descriptor validation, single-link/no-symlink checks, a nonblocking lock and atomic file replacement. **This is plaintext storage, not encryption or an OS keychain.** Other code running as your OS user (including trusted extensions and approved host commands) can access it. Use your operating system's disk encryption and protect your account. Codine model file tools exclude this entire directory, including inactive keys and temporary files, even when your project is your home directory.

Existing shell and `.env` precedence remains unchanged. Saved credentials are a fallback only when no key setting exists. An explicitly empty key in a higher-priority source disables that fallback. `/settings` identifies the effective key source without printing it. Keys loaded into runtime configuration enter the existing secret redactor before provider construction. A running process uses its startup snapshot: restart after changing credentials.

Custom provider credentials use `codine auth set-key custom --endpoint URL` and are bound to the canonical exact endpoint. They are never reused for a different endpoint or a built-in provider. Custom endpoints retain their explicit trusted endpoint/key configuration and billing acknowledgement requirements; saving a credential grants no new network authority.

`logout` removes only the saved local entry. It does **not** revoke the remote API key, erase shell/`.env` values, or clear a key from an already running process. Revoke compromised/unwanted keys in your provider dashboard, remove obsolete environment settings yourself, and restart Codine. No remote revocation requests are made automatically. An interrupted exchange or failed storage after authorization may leave a valid provider-side key; use the provider dashboard to inspect/revoke it.

## OpenRouter flow and limits

The implementation follows the official [OpenRouter PKCE guide](https://openrouter.ai/docs/guides/overview/auth/oauth). Authorization opens OpenRouter in your browser; Codine receives a new API key after code exchange. It never reads your browser cookies or provider password. A fresh S256 verifier and state are generated for each attempt.

The callback listens only on IPv4 `127.0.0.1` at an OS-assigned port, with an unpredictable path. It requires the exact Host/path, one matching state and one bounded code; duplicate fields, absolute request targets and malformed requests are rejected without echoing input. Callback waiting is capped at 120 seconds, individual requests at one second and 8 KiB. Callback responses forbid caching and contain no code/key.

Exchange uses only the fixed OpenRouter HTTPS endpoint with normal certificate verification, no redirects, ambient proxy credentials or existing bearer key. One attempt runs in a dedicated minimal-environment child, bounded to 20 seconds overall, 15-second socket operations and 16 KiB response data. No exchange retries occur. Cancellation terminates the exchange child. Verifiers, codes and keys never enter process arguments or error messages. No inference call or balance check follows login.

Headless/browserless environments should use `set-key`; automatic SSH port forwarding and OpenRouter's separate headless authorization flow are not implemented. OS-keychain storage is not implemented. Browser authorization behavior is covered with fake exchange transport and a local callback fixture; no real login or key creation was performed during development.
