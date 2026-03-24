# Contributing to MeetingTranslatorNetwork

Thank you for your interest in contributing to MeetingTranslatorNetwork.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/MeetingTranslatorNetwork.git
   cd MeetingTranslatorNetwork
   ```
3. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```
4. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Guidelines

- **Python version**: 3.11+
- **UI framework**: PyQt6
- **Code style**: Follow existing conventions in the codebase
- **Commits**: Write clear, descriptive commit messages in English
- **Testing**: Test your changes locally before submitting a pull request

## Project Structure

See [ARCHITECTURE.md](ARCHITECTURE.md) for a detailed overview of the codebase.

## Pull Requests

1. Ensure your changes work on both Windows and macOS where applicable
2. Update documentation if your changes affect user-facing behavior
3. Keep pull requests focused on a single concern
4. Describe what your PR does and why

## Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include steps to reproduce for bug reports
- Specify your OS, Python version, and relevant configuration

## Security

If you discover a security vulnerability, please report it responsibly. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
