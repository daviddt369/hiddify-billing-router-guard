# Contributing

Thank you for your interest in this project.

This is an independent community addon stack for [Hiddify Manager](https://github.com/hiddify/HiddifyPanel).
It is not affiliated with or officially supported by the Hiddify project.

---

## How to contribute

### Reporting bugs

Open a GitHub issue with:

- the exact command you ran
- the error message or log output (mask any tokens, IPs, or domain names)
- OS version and HiddifyPanel version
- whether this is a clean install or an upgrade

### Suggesting improvements

Open a GitHub issue describing what you want to achieve and why.
Pull requests are welcome for bug fixes and documentation improvements.

### Submitting a pull request

1. Fork the repository.
2. Create a branch from `master`: `git checkout -b fix/your-description`
3. Make your changes. Do not bundle unrelated changes in one PR.
4. Test on a clean VM with HiddifyPanel 12.0.0 if possible.
5. Run syntax checks before submitting:

   ```bash
   # Shell scripts
   git ls-files release/ | grep '\.sh$' | while read -r f; do bash -n "$f" && echo "OK: $f"; done

   # Python files
   git ls-files release/ | grep '\.py$' | while read -r f; do python3 -m py_compile "$f" && echo "OK: $f"; done
   ```

6. Run a secret scan:

   ```bash
   git grep -E '[0-9]{8,12}:[A-Za-z0-9_-]{30,45}'
   git grep -E 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
   ```

7. Open a pull request against `master`.

---

## Security issues

Do not open public issues for security vulnerabilities. See [SECURITY.md](SECURITY.md).

---

## Code style

- Shell scripts: `set -Eeuo pipefail`, no `eval`, no `rm -rf` on non-temp paths.
- Python: follow the existing style in each file; no hardcoded secrets.
- Documentation: no real IPs, domains, tokens, or UUIDs. Use placeholders.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
