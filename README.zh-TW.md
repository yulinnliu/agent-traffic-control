# Agent Traffic Control

誰獲准寫入、一條分支一個寫入者、session 之間乾淨交接。給一位 owner
同時驅動多個 coding agent、橫跨多個 repository 的情境。不是 gateway，
不是 orchestrator。

[English](README.md)

## 為什麼需要

讓 Claude Code、Codex 和你自己的工具對同一批 repository 工作，會出現
單一 harness 都解不了的失敗：

- **兩個 session 寫同一條分支。** 各自對自己的 diff 都是對的，對分支都是錯的。
- **從過期的 SHA 接手。** 交接筆記說的是當時的位置，分支早就往前走了。
- **摘要說「做完了」，git 顯示沒推。** 下一個 session 相信了文字，沒相信 repository。
- **A 工具建的 worktree 被 B 工具清掉。** 沒有任何地方記錄它是誰的。
- **Agent 因為能推就推了。** 把「拿得到憑證」當成「有權限」。

每種失敗對應這裡的一個機制，而每個機制都錨定在 git 上，因為 git 是
每個 agent、每種語言都已經有的東西。

| 失敗 | 機制 |
| --- | --- |
| 併發寫入 | 一條 topic branch 同一時間只有一個 active writer；writer 轉移必須顯式，且不附帶 publish 權限 |
| 過期接手 | 每次交接都寫明 base SHA、target SHA 與驗證狀態；接手方在第一次修改前重新檢查 ref |
| 文字凌駕 git | 真相順序：runtime 證據、git 與檔案、設計紀錄、交接筆記、工具私有記憶 |
| 孤兒 worktree | owner、topic、session 寫在 worktree 的 git lock reason 裡，任何工具都讀得到 |
| 把能力當權限 | 有效權限是任務授權、governance、專案政策三者的交集；hard deny、approval required、permission preset 都是資料，產生成各 harness 自己的設定 |

## 這裡有什麼

| 路徑 | 內容 |
| --- | --- |
| `shared/governance/` | 每個 agent 以固定版本讀取的規則：`CORE.md` 加上由 `INDEX.md` 路由的模組 |
| `shared/policies/` | hard deny、approval required、permission presets、escalation gate 的 JSON Schema 與範例 |
| `shared/skills/` | 跨工具、跨 repository 共用的 canonical skill；`.claude/`、`.agents/`、`.dsh/` 放由它們 render 出來的受管副本 |
| `shared/templates/` | onboarding 插入專案 `AGENTS.md` 的政策區塊 |
| `shared/conformance/` | 語言中立的 fixture，任何工具實作都必須通過 |
| `scripts/` | 不需要任何語言 runtime 的 POSIX sh 入口：安裝與檢查 skill adapter |
| `adapters/python/` | 工具的 Python 參考實作 |
| `workspace.yaml.example` | 本機專用的對照表：專案名稱對應 repository、worktree、state 三個根目錄 |

合約就是檔案：Markdown 規則、YAML 政策、JSON Schema、git metadata。
工具是這些合約可替換的實作。Python 是參考實作，不是必要條件。

## 安裝

先決條件：git。完整 workspace 路徑另外需要 [uv](https://docs.astral.sh/uv/)。

### 只要 skills，不需要 Python

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
atc/scripts/link-skills.sh path/to/your-repo     # 所有 baseline skill
atc/scripts/link-skills.sh path/to/your-repo hami-worktree hami-writing-for-agents
```

`link-skills.sh` 把每個 skill 以完整受管副本複製進 `.claude/skills/`、
`.agents/skills/`、`.dsh/skills/`，把 governance 規則 vendor 進
`.atc/governance/` 並在 `.atc/governance.yaml` 釘住來源與 revision，
repository 沒有 `CLAUDE.md` 時補上指標檔，並把 governance 區塊插進
`AGENTS.md` 開頭。之後這個 repository 不依賴任何外部東西：沒有環境變數，
也沒有指回本 checkout 的路徑。repository 自己的檔案只回報、不動。`--link`
改用 symlink；`--prune` 移除正本已不存在的 adapter。Windows 請在 Git Bash 執行。

會執行工具的 skill（`hami-worktree`、merge preflight）呼叫的是 `atc`
指令，每台機器裝一次放上 PATH：

```sh
uv tool install -e atc/adapters/python
```

### 完整 workspace

```sh
git clone https://github.com/yulinnliu/agent-traffic-control.git atc
cd atc/adapters/python
uv run --locked python scripts/bootstrap.py init                        # 建立 workspace.yaml 與 projects/ worktrees/ state/
uv run --locked python scripts/bootstrap.py add my-project --repo <url> # clone、登記、onboard、驗證
uv run --locked python scripts/bootstrap.py status                      # doctor 加上 onboarding 乾跑檢查
```

`add` 可加 `--skills hami-worktree,hami-writing-for-agents` 在 baseline 之外
安裝 opt-in adapter；省略 `--repo` 時會使用你已放在 `projects/<name>` 的
checkout。這條路徑多了 worktree、preflight、policy 與 friction 工具，全部收在
`atc` 一個指令底下（`uv run atc --help`，或 `uv tool install -e .` 放上
PATH）；預期的目錄配置見 `docs/LAYOUT.md`。`atc` 會從工作目錄往上找最近的
`workspace.yaml`，找不到就用它被安裝時的 checkout；`ATC_ROOT` 只是可選的
覆寫，不是必要條件。

### 讓安裝的檔案保持最新

專案裡的 skill 檔與 `.atc/governance/` 都是 render 出來的副本，本 repo 更新
後是重新 render、不是手改：

```sh
atc/scripts/check-skills.sh path/to/your-repo                           # current / drifted / orphaned / stale
atc/scripts/link-skills.sh --prune path/to/your-repo                    # 修復
uv run atc bootstrap sync --prune                                       # 所有登記的專案，接著驗證
```

每個 render 出來的檔案都帶 `managed-by: agent-traffic-control`；沒有這個
標記的檔案屬於專案，永遠不會被動到。

## Skills

未標註者為 model-invoked。

- **hami-worktree** 建立、恢復、稽核、清理身分存在 git 裡的 per-topic worktree。
- **hami-handoff-reminder** 判斷該交接、壓縮還是寫交接筆記。
- **hami-retro** 回顧 session 紀錄並提出改進。
- **hami-writing-for-agents** 撰寫 skill、`AGENTS.md` 與 pointer 的參考。
- **hami-shared-skill** 建立共用 skill 與它 render 出來的各工具副本。
- **hami-token-audit** 稽核 repository 反覆發生的 context 成本。
- **hami-token-hygiene** 委派吵雜工作時讓 session 維持便宜。
- **hami-offload-to-chatgpt**（user-invoked）準備可自足的研究 prompt 給網頁模型。

## 範圍

`SCOPE.md` 說明什麼屬於這裡、接受哪些 issue。`.out-of-scope/` 記錄
試過或考慮過而拒絕的概念，附證據。`GLOSSARY.md` 定義規則使用的術語。

## 狀態

目前版本 0.1.0（git tag `v0.1.0`，Python adapter 同版號）。Governance、
政策、schema、skills、conformance fixture 與 Python 參考實作皆已就位。CI
以 schema 驗證政策範例、跑 shell 安裝腳本，並在 Linux 與 Windows 執行
Python 測試套件。裝進 repository 的檔案本身沒有格式版本：與這個版本
render 出來的內容相同就是最新，否則 sync 會直接換掉。`atc enforce` 依
存取模式產生 hard deny，拒絕無法解析的 token，`--check` 也能比對已安裝的
設定；有 `workspace.yaml` 時另依各專案的 state root 產生本機 deny，不進
`generated/enforcement/`。`atc validate --projects` 會檢查專案的 enforcement 產物，並在 `main`
上確認 governance pin 仍在 `main` 的歷史裡。

## 授權

MIT。
