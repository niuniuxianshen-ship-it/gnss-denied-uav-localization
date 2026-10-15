# 离线批量任务准备 v0.1：使用与核查说明

**仅离线准备，未冻结。** 本目录的DEMO01、DEMO02是程序生成的教学图片，**不是研究样本**。四项产物是图片×条件的计划任务，状态均为“尚未调用”，模型调用数为0。

## 文件入口

- [prepare_tasks.py](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/prepare_tasks.py)：读取显式JSON配置，验证编号、文件存在及预期SHA-256，再生成完整笛卡尔积清单。
- [generate_demo_images.py](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/generate_demo_images.py)：用标准库生成两张96×64简单几何PNG；图内tEXt声明“teaching only; not a research sample”。
- [test_prepare_tasks.py](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/test_prepare_tasks.py)：七项CLI检查；临时配置和输出只放在本目录的临时子目录，结束自动清理。
- [教学配置.json](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学配置.json)、[教学任务清单.json](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学任务清单.json)、[离线核查结果.json](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/离线核查结果.json)：分别保存显式输入、四项计划任务及实际核查证据。
- [教学材料说明](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学材料_不是研究样本/说明.md)；图片：[DEMO01.png](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学材料_不是研究样本/图片/DEMO01.png)、[DEMO02.png](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学材料_不是研究样本/图片/DEMO02.png)；提示词：[基础副本](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学材料_不是研究样本/提示词/基础条件草案.txt)、[增强副本](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/批量入口离线准备_v0.1/教学材料_不是研究样本/提示词/增强条件草案.txt)。

提示词副本逐字节复制原件，未改正文、换行或编码；教学用途由目录名、材料说明和配置标注。原文仍提及共同参考附录，本轮没有组装该附录，因此这些计划不能直接视为完整模型请求。

## 环境、配置与启动

本机实际检查使用 **Python 3.10.0，Windows，全部为标准库**，无须安装依赖。其他Python版本未验证。入口的控制台输出显式采用UTF-8。

从本目录启动，以下入口命令**已实际离线执行成功**：

```powershell
python -B .\prepare_tasks.py --config '.\教学配置.json' --output '.\教学任务清单.json'
```

交付的清单已存在，再执行同一输出路径会拒绝覆盖（已实际检查）。若需再次生成，显式指定一个尚不存在的输出文件；不要覆盖本轮清单。程序不自动建立输出父目录。相对输入路径按**配置文件所在目录**解析；绝对路径也可显式提供，程序仅读取配置及其中列出的图片、条件文件，不扫描候选池或自动补充材料。

配置schema_version为1；batch_id为批次编号；images/conditions均为非空列表，各项含image_id或condition_id、path、sha256、material_notice。编号限1—64个ASCII字母、数字、下划线或连字符，首字符为字母或数字；图片编号和条件编号各自在所属列表内不得重复。预期哈希按原始文件字节检查，任一输入缺失或不符均报错，退出码为2，不写清单。成功退出码为0。输出以独占创建方式保存，已存在的文件不覆盖。

任务编号为batch_id:image_id:condition_id，三段编号均不允许冒号。教学配置批次为DEMO_BATCH_001；批次编号的跨批唯一性需要使用者维护，本入口不读取或改写全局登记。清单保存解析后的绝对输入路径和实际SHA-256、配置路径与哈希、各项教学声明及状态。目录搬迁后应按新位置另存清单，保留本轮产物。

## 四项计划任务（不是模型运行）

|图片编号|条件编号|唯一任务编号|状态|
|---|---|---|---|
|DEMO01|BASE|DEMO_BATCH_001:DEMO01:BASE|尚未调用|
|DEMO01|ENHANCED|DEMO_BATCH_001:DEMO01:ENHANCED|尚未调用|
|DEMO02|BASE|DEMO_BATCH_001:DEMO02:BASE|尚未调用|
|DEMO02|ENHANCED|DEMO_BATCH_001:DEMO02:ENHANCED|尚未调用|

## 实际离线检查与复现

以下检查命令**已实际执行**，最终七项全部通过：

```powershell
python -B .\test_prepare_tasks.py
```

|检查|实际结果|
|---|---|
|正常配置|四项恰好覆盖两图×两条件；任务编号唯一；路径与哈希对应；状态均为尚未调用|
|重复图片编号／重复条件编号|两项均拒绝，退出码2；不生成输出，不改写已有产物|
|图片／条件预期哈希不符|两项均拒绝，退出码2；输入与已有产物不变|
|输出已存在|拒绝覆盖本轮教学清单，退出码2；原清单哈希不变|
|输入缺失|拒绝继续，退出码2；不生成输出|
|源码静态检查|三个新脚本语法有效、仅标准库；未发现网络请求、凭据查找或旧运行器导入／执行路径；检查脚本只启动新prepare_tasks.py|
|教学PNG|签名、CRC、96×64尺寸、像素解压长度及教学声明检查通过|
|临时文件|检查结束没有临时检查目录或__pycache__遗留|

首次检查出现Windows默认输出编码与UTF-8读取不一致，导致四项检查报错；仅修正新入口stdout/stderr编码后，七项检查通过。此失败及修正保存在离线核查结果.json，不冒称首次全部通过。旧运行器从未导入或执行，其默认离线函数也未运行。

## 实际全文读取及原件保留

四份指定输入均全文读取，以下SHA-256为读取基线；保存及检查后逐项核对一致。两份提示词教学副本同时与原件逐字节一致。

|原件|SHA-256基线|保存后核对|
|---|---|---|
|[DeepSeek试跑/run_trial.py](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/DeepSeek试跑/run_trial.py)|95561b88319b4dc28c163800e7a53b616188cb573413ba1a8a0e1f29dee73ccc|一致；全文读取|
|[DeepSeek试跑/使用说明.md](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/DeepSeek试跑/使用说明.md)|f258d51e81875c2e5fe4983e9615a49269f88c77c78f0d9b964d749d744dacaf|一致；全文读取|
|[候选比较条件草案_v0.1/基础条件草案.txt](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/候选比较条件草案_v0.1/基础条件草案.txt)|a2c26cef95d19affca38b7ffb0b1fdf664f35d5ae1fc8b0606a92c38ec582f56|一致；全文读取|
|[候选比较条件草案_v0.1/增强条件草案.txt](C:/Users/leimaren/Documents/Codex/2026-10-09/6-2-x20/outputs/视觉定位流程实验/候选比较条件草案_v0.1/增强条件草案.txt)|5f6202a8a9ca7d59274535d49814580b3cf1ee486490f35a180d893f5c30caa4|一致；全文读取|

本轮仅在这个新目录创建交付物。原件、旧运行器、历史请求、响应和实验记录保持原位原文。

## 限制及未解决事项

共同附录、正式图片与标签、最终实验条件、接口载荷、模型和采样设置、预算及正式执行授权仍待后续确定。没有实现定位或评分API、评分、比例统计、自动补样本，也没有继承旧运行器的001.png、A/B/C或deepseek-flash/思考强度/输出预算等固定参数。

本轮未读取密钥、联网或调用模型；没有重跑历史实验，不冻结方案。离线成功仅证明任务组合和输入保护在本轮教学材料上可执行，不证明API兼容性或实验效果，不改变其他研究阶段。
