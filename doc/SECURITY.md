# Security and network access

The server is local-first and binds to `localhost` by default. Private HTTP
routes and all WebSocket entry points enforce the same access policy:

- the request `Host` must be local or explicitly trusted;
- browser `Origin` must match that trusted host;
- origin-less native requests are accepted only from loopback;
- the packaged Electron renderer's non-web origin is accepted only from
  loopback with an Electron user agent;
- internal MCP bridge routes additionally require a random per-run token.

These controls prevent an unrelated website from reading Gmail metadata,
driving the agent, using ASR/TTS resources, or changing local furniture state
through the browser's access to localhost.

Router factories embedded in another FastAPI application default to the same
loopback-only host policy when `app.state.trusted_hosts` is not set. Embedders
must install an explicit allowlist before serving those routes on a network.

## LAN access

To serve the application to another device, bind the socket to all interfaces
and explicitly list every hostname or IP address users will put in the URL:

```yaml
system_config:
  host: '0.0.0.0'
  port: 12393
  trusted_hosts:
    - '192.168.1.10'
    - 'companion.lan'
```

`trusted_hosts` accepts exact hostnames and IP addresses only. Do not include a
scheme, path, port, or wildcard. Keep the list minimal and use a host firewall
to limit which networks can reach the port. Binding to `0.0.0.0` without adding
the LAN address to `trusted_hosts` remains loopback-only at the application
layer.

## Agent workspace and downloads

Workspace file operations resolve and verify paths before access. Instruction
files under `skills/` remain read-only to the shell tool, which runs in a
network-disabled container by default.

The download tool accepts only public HTTP(S) destinations. It resolves and
validates every redirect, connects to the validated IP address directly, and
retains the original hostname for the HTTP `Host` header and HTTPS certificate
verification. This prevents DNS rebinding from redirecting a validated download
to a loopback or private-network service.
