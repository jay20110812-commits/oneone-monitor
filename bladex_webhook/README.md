# OneOne Discord Monitor

Monitors oneone search results and sends Discord webhook notifications when matching products change and the configured big-prize probability threshold is met.

## GitHub Actions

Set this repository secret:

- `DISCORD_WEBHOOK_URL`

The workflow runs every 5 minutes and can also be started manually from the Actions tab.
