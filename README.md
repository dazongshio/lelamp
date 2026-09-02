# LeLamp

![LeLamp Banner](./docs/assets/images/README/Banner.png)

An open source robot lamp based on [Apple's Elegnt](https://machinelearning.apple.com/research/elegnt-expressive-functional-movement), made by [Human Computer Lab](https://www.humancomputerlab.com/)

## Overview

![LeLamp in real life](./docs/assets/images/README/lelamp_irl.jpg)

**Build specs:** ~$260 cost | 8-12 hours | Intermediate skill level

LeLamp is an expressive robot lamp featuring:

- 🎭 **5-axis articulated movement** with servo motors
- 📸 **Computer vision** via Pi Camera
- 🎤 **Audio interaction** with microphone and speaker
- 💡 **LED expressions** with 24 programmable LEDs
- 🎮 **Record & replay** custom movements

## Software Console

This repository also contains the LeLamp office console used on the Raspberry Pi:

- `src/`: React and TypeScript user interface
- `lelamp_runtime/`: Python hardware and office-assistant runtime
- `scripts/collaboration-server.mjs`: Yjs/Hocuspocus document collaboration service
- `scripts/start_fixed_console.sh`: production API and web-console entry point
- `config/runtime-defaults.env`: shared non-secret defaults used by the Web Console and collaboration service

The production web console and API use port `8790`; collaborative document WebSockets
use port `8791`. The Python service serves the built frontend, so Vite is only needed
for frontend development.

### Run on the LeLamp device

Install frontend dependencies and build the current UI:

```bash
npm ci
npm run build
```

Start the installed user services:

```bash
npm run services:start
npm run services:status
```

After changing backend or frontend code, rebuild and restart both services:

```bash
npm run build
npm run services:restart
```

For local frontend development, keep the API on port `8790` and run:

```bash
npm run dev
```

Vite listens on port `5173` and proxies `/api` requests to the Python service.
The API requires the `LELAMP_WEB_TOKEN` configured in `lelamp_runtime/.env`.

Run the software checks without accessing physical hardware:

```bash
npm run check
```

Collaborative document deployment, backup, and recovery are documented in
[`docs/operations/collaborative-documents.md`](./docs/operations/collaborative-documents.md).

## Build Guide

**Start here:** [Prerequisites & Planning](./docs/0.%20Prerequisites.md)

### Complete Build Process

1. [📋 Prerequisites & Planning](./docs/0.%20Prerequisites.md) - Skills, tools, and BOM
2. [🔧 Components & 3D Parts](./docs/1.%203D%20Print.md) - Components and 3D Printed Models
3. [🎯 Lamp Assembly](./docs/3.%20LeLamp%20Assembly.md) - Mechanical and electrical assembly
4. [🏗️ Lamp Setup](./docs/4.%20LeLamp%20Setup.md) - Runtime Setup
5. [🎮 Lamp Control](./docs/5.%20LeLamp%20Control.md) - Controlling LeLamp
6. [🔍 Common Issues](./docs/6.%20Common%20Issues.md) - Common issues and solutions

### Quick Reference

- **Bill of Materials**: [Complete component list with suppliers](./docs/0.%20Prerequisites.md#bill-of-materials)
- **3D Print Files**: Available in `/3D/` directory ([OnShape CAD](https://cad.onshape.com/documents/16c9706360b5ad34f9c8db49/w/2edfa54c83253c120fbc9e58/e/a7196194821d9cfe2842a44a))
- **Order the Kit**: [Register interest here](https://docs.google.com/forms/d/e/1FAIpQLSfOXO2q_I2LKqYE0LoPN8VtrpKWrvJ1OkRAiS1iBFML1eqoGw/viewform?usp=sharing&ouid=105369619268976630712)
- **Software Control**: [LeLamp Runtime Repository](https://github.com/humancomputerlab/lelamp_runtime)

## Project Status

⚠️ **Early Development** - This project is actively being developed. We published our progress early to encourage community feedback and iteration.

## Community & Support

### Getting Help

- **Start here**: [Troubleshooting Guide](./docs/6.%20Troubleshooting.md) for common issues
- **Discord**: [Join our community](https://discord.gg/727JXBt8Zt) for real-time help and discussions
- **GitHub Issues**: [Report bugs or ask questions](https://github.com/humancomputerlab/le_lamp/issues)

### Contributing

We welcome contributions:

- 🐛 Bug reports and fixes
- 📖 Documentation improvements
- 🔧 Design enhancements
- 🌍 Regional supplier information
- 📸 Build photos and videos

Please check existing issues before creating new ones.

## Maintainers
Maintained by [Human Computer Lab](https://www.humancomputerlab.com).

## Acknowledgments & Sponsors
See [CONTRIBUTORS.md](./CONTRIBUTORS.md) for contributors and their roles.  
See [SPONSORS.md](./SPONSORS.md) for sponsor thanks and how to support the project.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Local AI device control (MHS-ready)

LeLamp now includes a model-neutral device adapter and a local MCP stdio server.
It describes device state, safe procedures, and physical safety constraints in a
form that Codex or another MCP client can discover. This is an MHS-ready bridge,
not a claim of compliance with Anthropic's unreleased official MHS SDK.

Start a protocol session manually:

```bash
cd /home/lemp/lelamp/lelamp_runtime
.venv/bin/python -m lelamp.office_agent.mhs_mcp_server
```

The MCP tools are `lelamp_describe`, `lelamp_read_status`,
`lelamp_set_expression`, `lelamp_play_safe_motion`, `lelamp_observe_camera`, and
`lelamp_emergency_stop`. Physical and camera actions require `confirmed=true`;
emergency stop never requires confirmation. Motor-register and raw-serial tools
are intentionally not exposed. Hardware writes remain disabled until
`OPENCLAW_ENABLE_HARDWARE=1` is supplied to the MCP process, while RGB also needs
`OPENCLAW_ENABLE_RGB=1`.
