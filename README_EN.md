<img align="right" width="320" src="./depends_data/readme/neuro.gif" alt="PROJECTNEURO_GIF"/>

<br/>

![PROJECTNEURO_SVG_GITHUB_DARK](./depends_data/readme/project-neuro.svg#gh-dark-mode-only)
![PROJECTNEURO_SVG_GITHUB_LIGHT](./depends_data/readme/project-neuro-dark.svg#gh-light-mode-only)  

An Open-Source AI Agent Application With MCP Based On Python

- Code Agent
- Worker Mode (In Development)
- Tool Calling
- Data Collection

<br clear="right"/>

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Newsreader&size=26&pause=1000&center=true&vCenter=true&multiline=true&width=800&lines=We+Choose+To+Go+To+The+Moon+++---+John+F.+Kennedy)](https://git.io/typing-svg)

<div align="center">
    <p>
        <img src="https://img.shields.io/badge/Python-3.10+-blue" alt="Python Version">
        <img src="https://img.shields.io/github/license/Fedal987/neurocode-py?label=License" alt="License">
        <img src="https://img.shields.io/badge/Status-In%20Development-yellow" alt="Status">
        <img src="https://img.shields.io/github/contributors/Fedal987/neurocode-py.svg?style=flat&label=Contributors" alt="Contributors">
        <img src="https://img.shields.io/github/forks/Fedal987/neurocode-py.svg?style=flat&label=Forks" alt="Forks">
        <img src="https://img.shields.io/github/stars/Fedal987/neurocode-py?style=flat&label=Stars" alt="Stars">
    </p>
    <p>
        <a href="README.md">简体中文</a> | <a href="README_EN.md">English</a>
    </p>
</div>

---

## What Is This?

ProjectNeuro is an open-source agent application based on MCP and tool calling, developed by fans of the AI VTuber [Neuro-sama](https://www.twitch.tv/vedal987).  
It is not limited to coding: our upcoming Worker Mode will offer an entirely different experience.

---

## How Do I Deploy It?

### Deploy from Source

If you are more comfortable with the command line or would like to contribute to development, you can deploy ProjectNeuro from source.

### Windows

#### Step 1 — Install [Git](https://git-scm.com/) and [Python](https://www.python.org/)

- Visit https://www.python.org/ftp/python/3.14.7/python-3.14.7-amd64.exe to download the latest version of Python.

(Documentation to be completed)

- Visit https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/Git-2.55.0.5-64-bit.exe to download the latest version of Git.

(Documentation to be completed)

#### Step 2 — Clone the Source Code

Step 2.1: Clone the ProjectNeuro source code with Git.

```bash
git clone https://github.com/Fedal987/ProjectNeuro.git
```

#### Step 3 — Configure the Environment

Step 3.1: Install the `uv` package manager with Python, then create and activate a virtual environment.

```bash
pip install uv
cd ProjectNeuro
uv venv
.venv\\Scripts\\activate
```

Step 3.2: Install the runtime dependencies.

```bash
uv pip install -r requirements.txt
```

Step 3.3: Copy `config.toml.bak` from the `templates` directory to the project root and rename it to `config.toml`.

Step 3.4: Fill in the configuration file.

```toml
[API_MANAGER]
BASE_URL = "https://api.siliconflow.cn/v1" # Replace this with the URL of your actual provider
API_KEY = "" # Enter the API key supplied by your provider
MODEL = "deepseek-ai/DeepSeek-V4-Flash" # Model currently in use
TEMPREATURE = 0.7 # Usually does not need to be changed
STREAM = true # Usually does not need to be changed

[REASONING]
ENABLED = true # Enable the reasoning prompt and thought display; the shared tool-calling agent is still used when disabled
THINKING = true # Enable thinking mode for supported DeepSeek/SiliconFlow models
MAX_STEPS = 12 # Maximum number of model execution rounds allowed for a single task
EFFORT = "" # Set to low/medium/high when supported by the API; leave empty for broader provider compatibility
AUTO_APPROVE = false # When false, ask the user for confirmation before writing files or running commands
COMMAND_TIMEOUT = 60 # Command timeout in seconds
```

#### Step 4 — Run ProjectNeuro

```bash
# Make sure you are inside the ProjectNeuro virtual environment
# If the virtual environment is not active, run .venv\\Scripts\\activate first
uv run neuro.py
```

### Linux (Arch with the fish shell)

#### Step 1 — Install [Git](https://git-scm.com/) and [Python](https://www.python.org/)

- Install `Git` and `Python` with `pacman`.

```bash
sudo pacman -S git
sudo pacman -S python
```

#### Step 2 — Clone the Source Code

Step 2.1: Clone the ProjectNeuro source code with Git.

```bash
git clone https://github.com/Fedal987/ProjectNeuro.git
```

#### Step 3 — Configure the Environment

Step 3.1: Install the `uv` package manager, then create and activate a virtual environment.

```bash
sudo pacman -S uv
cd ProjectNeuro
uv venv
source .venv\\bin\\activate.fish
```

Step 3.2: Install the runtime dependencies.

```bash
uv pip install -r requirements.txt
```

Step 3.3: Copy `config.toml.bak` from the `templates` directory to the project root and rename it to `config.toml`.

```bash
cp templates/config.toml.bak config.toml
```

Step 3.4: Fill in the configuration file.

```bash
vim config.toml
```

```toml
[API_MANAGER]
BASE_URL = "https://api.siliconflow.cn/v1" # Replace this with the URL of your actual provider
API_KEY = "" # Enter the API key supplied by your provider
MODEL = "deepseek-ai/DeepSeek-V4-Flash" # Model currently in use
TEMPREATURE = 0.7 # Usually does not need to be changed
STREAM = true # Usually does not need to be changed

[REASONING]
ENABLED = true # Enable the reasoning prompt and thought display; the shared tool-calling agent is still used when disabled
THINKING = true # Enable thinking mode for supported DeepSeek/SiliconFlow models
MAX_STEPS = 12 # Maximum number of model execution rounds allowed for a single task
EFFORT = "" # Set to low/medium/high when supported by the API; leave empty for broader provider compatibility
AUTO_APPROVE = false # When false, ask the user for confirmation before writing files or running commands
COMMAND_TIMEOUT = 60 # Command timeout in seconds
```

#### Step 4 — Run ProjectNeuro

```bash
# Make sure you are inside the ProjectNeuro virtual environment
# If the virtual environment is not active, run source .venv\\bin\\activate.fish first
uv run neuro.py
```

### macOS

> [!NOTE]  
> macOS deployment is still on the roadmap and has not yet been implemented. Please deploy from source for now.

### Deploy with Node.js

If you prefer the convenience of a quick npm deployment, you will be able to use this method.

> [!NOTE]  
> npm deployment is still on the roadmap and has not yet been implemented. Please deploy from source for now.

## TODO List

P0:
- Unify the model provider implementation between Agent and api_manager
- Fix sandbox security boundaries

P1:
- Refactor ToolRegistry into a tool object registration system
- Merge synchronous and streaming loops
- Use atomic writes for file modifications
- Implement Plugin API
- Implement Skill API
- Split terminal_cli into smaller modules
- Implement context compaction

P2:
- Migrate the src.main package to project_neuro
- Add automated PyPI publishing
- Implement Worker Mode
- Expand MCP support
- Try to bypass Anthropic's fuck Chinese(lol)

## Developers

- [Fedal987](https://github.com/Fedal987) (Project lead, documentation editor, and primary developer)
- [いじちにじか](https://github.com/Ij1chi-Nijika) (Primary developer, code reviewer, and project tester)

## Acknowledgements

- [DeepSeek](https://www.deepseek.com), whose release of DeepSeek-V3-0324 provided inspiration
- [MaiBot](https://github.com/Mai-with-u/MaiBot), which provided ideas for parts of the project
- GitHub, for providing code distribution services
- Artist [paccha](https://www.pixiv.net/users/96121842), who created the Neuro-sama artwork
- Members of the HeronStudio development team

## License

This project is open-sourced under the MIT License. Use, development, and redistribution of this project must comply with the terms of the MIT License.

<br/>

<p align="center">
    <a href="https://www.heronstudio.cc/">
        <img src="./depends_data/readme/HeronStudio.png" width="150" valign="middle">
    <a/>
    <br/>
    <b>Powered By HeronStudio</b>
    <br/>
    <b>Contact: fedal987@fedal.icu</b>
</p>
