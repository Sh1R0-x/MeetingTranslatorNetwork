# Security Policy

## Credential Storage

MeetingTranslatorNetwork stores API keys using OS-native secure storage:

- **Windows**: Windows Credential Manager via `keyring`, with DPAPI-encrypted fallback
- **macOS**: macOS Keychain via `keyring`
- **Linux**: Secret Service (GNOME Keyring / KDE Wallet) via `keyring`

No API keys or tokens are stored in plaintext configuration files.

## Reporting a Vulnerability

If you discover a security vulnerability in MeetingTranslatorNetwork, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Send a report via GitHub's private vulnerability reporting feature on this repository
3. Include a description of the vulnerability, steps to reproduce, and potential impact

We will acknowledge receipt and work on a fix promptly.

## Scope

This security policy covers:
- The MeetingTranslatorNetwork application code
- Credential storage and handling
- Build and packaging scripts

It does not cover:
- Third-party API services (Deepgram, AssemblyAI, OpenAI, Perplexity, HuggingFace)
- Third-party libraries (report upstream)
- User system configuration
