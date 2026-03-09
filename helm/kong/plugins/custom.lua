local client_ip = kong.client.get_ip() or "unknown"
kong.service.request.set_header("x-client-ip", client_ip)
kong.service.request.set_header("x-platform", "secure-api-platform")
