# 公众号内容自动飞轮（Qoder 版）

用 **Qoder + 飞书多维表格 + 微信公众号 API** 搭一条内容自动飞轮：

```
选题自动入库（飞书多维表格）→ 选题变文章（Qoder 写初稿）→ 排版推送草稿箱（微信官方 API）
```

对应萝卜哥分享的 WorkBuddy + 飞书流程，WorkBuddy 的角色由 Qoder（本技能）顶替。

## 目录结构

```
wechat-content-flywheel/
├── SKILL.md                    # Qoder 技能：三环飞轮编排流程
├── config.env                  # 凭据（自行创建，勿提交，600 权限）
├── config.example.env          # 配置模板
├── scripts/
│   ├── config.py               # 凭据加载
│   ├── feishu_btable.py        # 飞书多维表格客户端
│   └── wechat_api.py           # 微信公众号草稿箱客户端
├── studio/                     # 可视化操作台（浏览器五阶段页面）
│   ├── app.py                  # FastAPI 入口（127.0.0.1:8765）
│   ├── server/                 # 生成/排版/推送编排后端
│   ├── static/                 # 单页前端
│   └── requirements.txt
├── style-refs/                 # 历史文章（文风参考，放 .md/.txt/.html）
└── outputs/                    # 稿件与运行记录
```

## 一、注册技能到 Qoder

```bash
ln -s "$(pwd)" ~/.agents/skills/wechat-content-flywheel
```

之后在 Qoder 对话里说「帮我分析 10 个 AI 工具赛道的热门选题入库」即可触发飞轮。

## 二、准备飞书多维表格选题库

### 2.1 创建飞书应用

1. 打开[飞书开放平台](https://open.feishu.cn/)，创建「企业自建应用」。
2. 在「权限管理」中开通：
   - `bitable:app`：多维表格的读写（必须）
   - 可选 `docx:document:readonly`：如需让 Qoder 直接读飞书文档当文风参考
3. 在「版本管理与发布」中创建版本并发布，使权限生效。
4. 在「凭证与基础信息」中拿到 **App ID** 与 **App Secret**。

### 2.2 创建选题库多维表格

1. 在飞书里新建一个多维表格，名字随意（如「公众号选题库」）。
2. 建一张数据表，添加以下字段（名称必须完全一致，类型正确）：

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| 选题标题 | 文本 | 必填 |
| 关键词 | 文本 | 必填 |
| 内容方向 | 文本 | 选填 |
| 流量预估 | 文本 | 选填 |
| 状态 | **单选** | 选项：`待定`、`选中`、`已推送`，必填 |
| 备注 | 文本 | 必填，写稿时的重要约束 |
| 文章标题 | 文本 | 写稿后自动回填 |
| 稿件路径 | 文本 | 写稿后自动回填 |
| 入库日期 | 文本 | 自动填入库当天日期（如 2026-09-15），供操作台「今天」筛选 |

3. 从浏览器地址栏复制两个 ID（URL 形如 `https://xxx.feishu.cn/base/{app_token}?table={table_id}&view=...`）：
   - `app_token`：多维表格 ID
   - `table_id`：数据表 ID

## 三、配置凭据

```bash
cp config.example.env config.env
chmod 600 config.env
# 编辑 config.env，填入飞书与微信的凭据
```

`WECHAT_APP_ID` / `WECHAT_APP_SECRET` 即公众号后台的开发者 ID/密钥；公众号后台还需把当前服务器出口 IP 加入 IP 白名单。

## 四、放入文风参考

把 1~3 篇你自己的历史文章（Markdown / 纯文本 / HTML）放进 `style-refs/`，Qoder 写初稿时会先读它们学习行文风格。不放也能跑，但初稿风格会偏泛。

## 五、验证

```bash
# 校验飞书表结构
python3 scripts/feishu_btable.py verify

# 试查选中状态选题
python3 scripts/feishu_btable.py list --status 选中
```

## 常用命令速查

```bash
# 批量写入选题（状态默认待定）
python3 scripts/feishu_btable.py add --json /tmp/topics.json --status 待定

# 查询选题
python3 scripts/feishu_btable.py list --status 选中 --keyword 关键词

# 回填状态/文章信息
python3 scripts/feishu_btable.py update --record-id recXXXX --status 已推送 --article-title "标题"

# 推送排版稿到公众号草稿箱（含图片上传、封面、回读校验）
python3 scripts/wechat_api.py push \
  --html /path/to/排版稿.html \
  --title "文章标题" \
  --author 小崽子AI \
  --outdir outputs/runs/2026-09-14-001
```

## 依赖

- Python 3.9+
- `lxml`（微信推送与 studio 排版）：`pip install lxml`
- `Pillow`（studio 默认封面绘制）：`pip install Pillow`
- 飞书脚本为纯标准库，零依赖
- studio 操作台：`pip install -r studio/requirements.txt`

## 六、可视化操作台（studio/）

在浏览器里走完「自动筛选 → 手动挑选 → 文案生成 → 文案预览 → 发送公众号」五阶段全流程，适合不想在对话里逐环节下指令的场景。

### 启动

```bash
pip install -r studio/requirements.txt
# config.env 额外配置文案生成 LLM（OpenAI 兼容，如 DeepSeek）
# LLM_API_BASE=https://api.deepseek.com
# LLM_API_KEY=sk-xxx
# LLM_MODEL=deepseek-chat
python3 studio/app.py
# 打开 http://127.0.0.1:8765
```

### 五个阶段

1. **自动筛选**：拉取飞书选题库，按状态/入库日期/关键词/流量预估筛选排序；页面上直接勾选并标记「选中」（也可点「去写作队列」）；「批量入库」可把对话里联网搜好的选题 JSON 数组粘贴后一键写入飞书（自动填入库日期与「待定」状态）。
2. **手动挑选（写作队列）**：展示状态 = 选中的选题，点「去写稿」进入第 3 步。
3. **文案生成**：页面直连 LLM，结合选题字段、备注约束与 `style-refs/` 文风参考生成初稿。支持八种风格及智能推荐：冷静新闻解读、问题解决教程、认知拆解、证据驱动评测、结论先行、对比决策、观点评论、案例复盘。按题材选择事实、结论、问题或对比开篇，不强制第一人称；亲历、实测与复盘需有真实材料，缺失项明确标注。自动推荐优先看标题和内容方向，避免备注中的附带建议误导题材；生成时读取 `outputs/` 日期命名母稿及 `outputs/runs/` 日期命名目录中的 `article.md`，选最近保存的 5 个不同开头，每段最多 240 字，仅用于避重复，跳过重复副本、测试目录、衍生平台稿件和原始推文导入包。内置首段后的 TL;DR 导读、3~4 个核心章节、重点加粗与按需使用的列表/表格；可在页面编辑，保存后写入 `outputs/` 并回填飞书。
   **完整性保障**：LLM 返回长度截断标记时，提高输出预算并从头重试一次（自动预算上限 16384 token）；即使服务返回正常结束，也检查章节数量、表格列数与代码块闭合，明显不完整时最多重写一次。重试后仍不完整则明确报错，不把半篇稿件显示为生成成功。生成和精简请求的页面等待上限为 10 分钟，给自动恢复留出时间。
4. **文案预览**：先由模型从当前标题和正文提炼核心主题、视觉主体与构图，按题材自动选择 Apple Glass / Terminal CLI / Blueprint Engineering / Data Dashboard / Pixel Arcade 风格，再调用生图模型生成主题插画。最终自动裁切为 `900×383` 横幅，并由视觉模型检查主题匹配、文字标注和主体裁切，发现问题时自动调整提示词重生成一次，整个封面环节无需人工选风格、设计提示词或提供图片。图片与创意保存在 `outputs/公众号封面/`，按标题、正文及生图配置缓存；标题或正文变化时生成对应新图，相同文章在不同主题和引擎中复用同一张封面。排版支持内置摸鱼绿/石墨极简/橄榄手记三套轻量主题，或 bmmd 引擎（16 款样式 + 14 款代码高亮，首次使用自动下载 CLI，需 Node ≥ 20）；列表/表格/引用间距自动归一化，Mermaid 流程图经 Chrome 光栅化后内嵌，也可导入 gzh-design-skill 产出的 HTML。首次封面生成与排版请求最多等待 10 分钟。
5. **发送公众号 / 离线导出包**：推送草稿箱（含图片上传、封面、回读校验），成功后回填飞书状态「已推送」；页面展示校验结果与历史推送记录。推送与离线导出复用预览时的 AI 封面。同页「公众号离线导出包」可把当前母稿一键导出成一个目录，含 `article.md`（源稿）、`article.docx`（Word 版，由公众号排版 HTML 直转，含同一张封面与光栅化后的流程图）、`wechat.html`（公众号排版）、`cover.jpg`（封面）、`cover-plan.json`（主题、构图、提示词与自检结果）与 `manifest.json`，统一落在 `outputs/runs/<时间>-<标题>/`，页面提供逐个下载，方便本地归档与备份。

   小红书与今日头条目前**只生成本地素材，不自动推送**：小红书和今日头条生成文案后，模型会结合正文核心内容、平台受众和 handraw-style 风格/构图/配色索引，调用配置的 Gemini 生图模型分别生成 3:4 与 16:9 主题封面；产出 Markdown、封面和 DOCX（头条），生成成功后页面提供「在 Finder 中定位文案」按钮，直接在 macOS Finder 中选中文件，再上传到对应平台。Playwright/CDP 自动推送入口已停用。

### 说明

- 自动封面服务复用 `LLM_API_BASE` / `LLM_API_KEY`，也可通过 `IMAGE_GEN_API_BASE` / `IMAGE_GEN_API_KEY` 单独配置；生图模型用 `IMAGE_MODEL`（兼容 `IMAGE_COVER_MODEL`），默认 `gemini-3.1-flash-image`。`IMAGE_COVER_SIZE` 默认 `1536x1024`，兼容服务拒绝该尺寸时自动重试方图，再裁为横幅。主题提炼与视觉自检使用 `LLM_MODEL`，该模型需支持图像输入。生图响应支持 Base64 和图片 URL。
- 小红书与今日头条封面使用 `.agents/vendor/handraw-style/` 的风格、构图和配色索引，由 `studio/server/platform_cover.py` 规划；实际图片仍由 `IMAGE_MODEL` 指向的 Gemini 生图模型完成。小红书使用 `IMAGE_XHS_COVER_SIZE`（默认 `1024x1536`），今日头条使用 `IMAGE_JRTT_COVER_SIZE`（默认 `1536x864`），失败时自动请求方图并裁切。封面原图与规划 JSON 缓存到 `outputs/平台封面/`，相同平台、标题和正文会复用。
- 本地全自动封面与预览入口：`.venv/bin/python -m studio.server.article_cover --markdown "outputs/日期-标题.md" --preview`，标题默认从文件名提取，也可由调用方传入 `--title`。重复运行命中内容缓存。
- 仅绑定 127.0.0.1，凭据只留在后端 `config.env`，浏览器不接触密钥。
- 与 Qoder 技能（SKILL.md）共用同一套飞书选题库与推送脚本，两种方式可混用。
- 可选依赖：Google Chrome（用于把初稿里的 Mermaid 流程图光栅化成微信可用的 PNG）；未安装时流程图会自动降级为代码块，不影响推送。
- 草稿同样只保存不发表，发布前最后一眼仍需人工把关。

## 与原文流程的对应

| 萝卜哥的飞轮 | 本实现 |
| --- | --- |
| WorkBuddy 连接飞书 | Qoder 运行本技能 + `feishu_btable.py` |
| 选题自动入库（含 AI HOT skill 提质量） | Qoder 联网调研 + 批量写入（`add`） |
| 飞书文档当行文风格约束 | `style-refs/` 历史文章目录 |
| 排版网站打包成 skill | `gzh-design-skill`（Qoder 内置） |
| WorkBuddy 推送草稿箱 | `wechat_api.py push`（含回读校验） |
| 不能无人值守 | 每环结束人工把关，草稿不自动发表 |
