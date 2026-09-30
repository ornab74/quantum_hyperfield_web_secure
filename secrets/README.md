# Secrets

Do not put plaintext secrets in the image.

The Flask application understands `<NAME>_FILE` variables. For example:

- `QNH_SECRET_KEY_FILE=/run/secrets/flask_secret_key`
- `OPENAI_API_KEY_FILE=/run/secrets/openai_api_key`
- `QNH_ADMIN_PASSWORD_HASH_FILE=/run/secrets/admin_password_hash`
- `WEAVIATE_API_KEY_FILE=/run/secrets/weaviate_api_key`

Mount those files read-only using Docker secrets, your orchestrator's secret
provider, or an OS-level credential manager.
