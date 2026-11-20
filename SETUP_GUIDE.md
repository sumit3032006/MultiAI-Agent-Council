# Setup Guide (Windows 10 / 11)

## 1. Extract the ZIP
Right-click `MultiAI-Agent-Council-v1.0.zip` -> **Extract All...** -> e.g. `C:\MAAC`. You get a folder `MultiAI-Agent-Council` with `START.bat`, `app.py`, `README.md`.

## 2. Install software
Only **Python 3.11 or newer**: https://www.python.org/downloads/ (tick **"Add python.exe to PATH"**). Check: `python --version`

## 3. Open the project folder in PowerShell
Open the folder in File Explorer, click the address bar, type `powershell`, Enter. Or: `cd C:\MAAC\MultiAI-Agent-Council`

## 4. Create the virtual environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, answer Y. (`START.bat` does steps 4-5 automatically.)

## 5. Install dependencies
```powershell
pip install -r requirements.txt
```

## 6. Get your API keys
* **OpenRouter** (free): https://openrouter.ai/keys -> Create Key. Make five keys (one per agent) or use one key for all agents. No credit card is needed for `:free` models.
* **Claude (optional)**: https://console.anthropic.com/ -> API keys. This is **paid** and billed to your Anthropic account; skip it if you only want free models.

Note: OpenRouter free-model limits (requests per minute/day) apply per account as far as I know, so five keys from the **same** account probably share one daily allowance. Check https://openrouter.ai/docs for current limits.

## 7. Where to put the keys - two ways
**A) In the dashboard (easiest):** start the app, open **Settings -> API keys**, paste a key per agent (or one shared key), press **Apply keys**. Tick *Also save to local .env file* to keep them for next time. Keys are shown masked afterwards.

**B) In the `.env` file:** rename `.env.example` to `.env` (`Rename-Item .env.example .env`), open it in Notepad, replace each placeholder with your real key (no quotes, no spaces), save:
```
OPENROUTER_API_KEY_RESEARCHER=sk-or-v1-...
OPENROUTER_API_KEY_TECHNICAL=sk-or-v1-...
OPENROUTER_API_KEY_CRITIC=sk-or-v1-...
OPENROUTER_API_KEY_INNOVATOR=sk-or-v1-...
OPENROUTER_API_KEY_JUDGE=sk-or-v1-...
```
One key for everyone: use the single line `OPENROUTER_API_KEY=sk-or-v1-...` instead. An agent's own key wins over the shared key.

## 8. How `.env` works
It stays on your computer only. The app never prints a full key; only the last 4 characters are shown. Never upload `.env` (it is in `.gitignore`).

## 9. Choose models and test the connections
Open **Settings**:
1. Press **Refresh free models from OpenRouter** - every currently free model appears in the model dropdowns (you can also type any model id).
2. Press **Auto-assign 5 different free models** (or pick manually per agent), then **Save agent settings**.
3. Press **Test connections** - one tiny request per enabled agent. `WORKING (1.2s)` = real success. `FAILED (auth)` = bad key. `FAILED (bad_model)` = model not available, pick another. `FAILED (quota)` = free allowance used.
To use Claude: enter the Anthropic key, then type or pick `claude-...` in an agent's model box and save. Claude usage costs money on your Anthropic account.

## 10. Start the application
Double-click `START.bat` or run `python app.py`. Open **http://127.0.0.1:7860/?__theme=dark** if the browser does not open.

## 11. Use the dashboard
**Council:** type a task (10-4000 characters), **Start Council**; **Stop** ends safely. Agent cards show status, model, activity, response time, API availability. "Safety limits" sets rounds, requests, tokens, seconds. **Discussion:** live messages. **Analytics:** usage. **History:** reopen/delete tasks. **Settings:** keys, models, roles.

## 12. Understand agent communication
Each message shows `Sender -> Receiver - TYPE - round N`. Types: PROPOSAL, QUESTION, CHALLENGE, DEFENCE, SUGGESTION, REVISION, EVALUATION (Judge progress check), VOTE (advisory), FINAL, SYSTEM (app notices). Round 1: proposals + the Critic's questions. Later rounds: agents answer messages addressed to them, challenge, revise. After each round the Judge decides whether open issues justify another round. The final answer lists agreements, disagreements, accepted/rejected proposals, uncertainties, recommendation.

## 13. Check API usage
**Analytics** tab: per key and model - requests, failures, rate-limit errors, tokens **as reported by the provider** (never guessed), availability. Cost shows "n/a" (free tier; Claude cost is not estimated).

## 14. Exhausted free quotas
That key's agent turns **QUOTA EXHAUSTED**, is skipped, and its key is not called again; agents with other keys continue. Agents sharing the same key share this state. If the Judge is exhausted another agent evaluates and the result is marked **PROVISIONAL**; if nobody can, **NOT FULLY REVIEWED**. After the allowance renews, press **Reset availability** in Analytics. Nothing is ever upgraded or purchased automatically.

## 15. Troubleshooting
* `python` not recognised: reinstall Python with "Add to PATH".
* "API key not usable": key missing/placeholder, or wrong prefix (OpenRouter keys start `sk-or-`, Anthropic `sk-ant-`).
* `FAILED (bad_model)`: free models change; press **Refresh free models** and pick another.
* "needs at least 2 agents": give at least two agents a working key and model.
* Port busy: set `MAAC_PORT=7861` in `.env`.
* Free models can be slow or temporarily rate-limited; the app waits and retries once.

## 16. Stop safely
Press **Stop** in the dashboard (history is kept), then **Ctrl+C** in the PowerShell/START.bat window and close it.
