# Security model

This application is intended for defensive observability and analysis.

## Trust boundaries

1. Browser input is untrusted.
2. Dataset labels/records are untrusted data, never instructions.
3. Vector-memory text is untrusted historical context.
4. Model output is advisory.
5. Action approval does not imply command execution; this web build exposes no
   arbitrary-command execution endpoint.
6. Container filesystem is read-only except `/var/data` and tmpfs `/tmp`.
7. Weaviate is private-network-only by default.

## Production checklist

- Use a unique Argon2 admin password hash.
- Supply a strong Flask secret using a secret file.
- Put the app behind HTTPS.
- Enable Secure cookies and HSTS after HTTPS is working.
- Keep the host bind address at 127.0.0.1 unless you have a firewall/VPN.
- Pin container image digests for production.
- Enable authenticated Weaviate if the Docker backend network is shared.
- Review dependency updates and produce a hash-locked dependency file in CI.
- Back up `/var/data` with host-side encryption and access controls.
- Do not mount the Docker socket into the application.
- Do not run the application container privileged.
