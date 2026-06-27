#!/usr/bin/env bash
# SAG-629 unblock: expose Paperclip web UI (127.0.0.1:3100) on the tailnet
# at https://gus-pinsoneault-framework.tail302fee.ts.net so the iPhone can
# hit it without sudo from future agent heartbeats.
#
# Run once on the Framework laptop:
#   bash setup-tailscale-serve.sh
set -euo pipefail

# 1. Let this user run `tailscale serve` without sudo from now on.
sudo tailscale set --operator="$USER"

# 2. Bind tailnet HTTPS 443 -> local Paperclip UI on 3100, persisted across reboots.
tailscale serve --bg --https=443 http://127.0.0.1:3100

# 3. Show the resulting config so we can confirm.
tailscale serve status
