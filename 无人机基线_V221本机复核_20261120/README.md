# V221 假正常复核

定位日志人工复核工具。它按真实 VIO 日志中的输入身份、双目一致性和八列分值标记需要复查的一秒窗口。可查看时段、帧号、原始时戳和触发依据，也可导出当前筛选的清单。

## 启动

使用 Windows 上的 Python 3.10 或以上版本。桌面版使用 Python 自带的 Tk。两版共用 `policy.json` 中的 V221 工作点和 `review_core.py` 的评分入口。

| 版本 | 启动方式 |
| --- | --- |
| Windows 桌面 | 双击 [启动Windows版.cmd](启动Windows版.cmd) |
| 本机网页版 | 双击 [启动网页版.cmd](启动网页版.cmd)，浏览器会打开启动窗口显示的 `127.0.0.1` 地址 |

也可在当前目录运行 `py -3 -B desktop.py` 或 `py -3 -B web.py --open`。网页版的日志在本机处理。关闭网页版启动窗口即可停止服务。

## 导入实际日志

1. 点击 **打开日志**。选择一份合并 CSV，或把下列三张表放在同一目录并同时选择。使用 UTF-8 编码，保留原帧号和纳秒时戳。
2. 在 **运行** 里选择要查的记录。先看橙色 **待复核**，对照同一时间的轨迹、图像或采集日志。再查看紫色 **证据不足**，核对对应的采集身份和特征缺项。
3. 点击时段查看原始时戳和该段峰值。用 **导出当前清单** 保存当前筛选结果。相对时间从该运行第一条记录起算，每个时刻对应一秒窗口末端。

| 项目导出 | 需要的列 |
| --- | --- |
| 上游动作 `joint_actions.csv` | `common`, `identity_status`, `eight_identity` |
| 双目证据 `scores.csv` | `constant`, `translation_3`, `rotation_3` |
| 八列特征 `features.csv` | `ready` 及下列八项特征 |

每表需要同一身份列 `parent_sequence,run_id,use_scope,input_seq,input_ns,condition`。`use_scope` 为 `relative_query_H1_at_end`。动作取值为 `allow`、`reject` 或 `abstain`。采集身份取值为 `match`、`mismatch` 或 `source_unavailable`。双目分数缺失时留空，连续三窗两列应同时存在或同时留空。`ready=0` 表示本槽八列评分缺少输入，相关特征留空。

八列特征为 `visual_inliers`、`visual_inlier_ratio`、`gyro_disagreement_deg`、`band_power_m2_s2`、`cadence_phase_max_ns`、`current_speed_m_s`、`speed_times_visual_inlier_ratio`、`speed_times_gyro_disagreement`。合并日志含上述身份、动作、双目和特征列。程序按帧身份对齐三份表。八列特征表可包含其他训练运行。

原始图像、视频、IMU 和 ROS bag 先经过项目的标定、VIO 回放与特征导出。此工具的直接输入是这些结构化记录。

## 如何判断

- **待复核**是规则或分数触发的提示。先读触发依据，再回看源数据。
- **证据不足**表示可用证据有缺项或接口无法判断。
- **未触发**表示当前证据未触发规则。仍可按时间查看与抽查。

八列分值用于定位和比较。导入电子表格时将 `first_ns` 与 `last_ns` 设为文本，保留精确时戳。这个工作点供人工离线复核。跨场景准确度与自动拦截仍需独立验证。

## 真实回放与来源

评分以 EuRoC V1_02 的自然回放及四种受控回放实核，共 8,550 个窗口。注入故障的条件在界面中标为受控回放。在有项目原始封存成果的工作区，可运行 `py -3 -B prepare_local_example.py` 生成本机示例，再点 **载入真实回放**。发布分支包含工具、模型参数和[验收记录](验收记录.md)。本机示例保存在 `local_data`。

- 训练和验证记录来自 EuRoC MAV Dataset 的项目回放。模型及对照指纹见参数文件。[ETH Zurich 数据来源](https://projects.asl.ethz.ch/datasets/euroc-mav/) [参数和指纹](policy.json)
- 两版用本机 Python 标准库、Tk 和浏览器运行。界面参考 Material 3 的颜色角色。[Google Material 3](https://m3.material.io/styles/color/the-color-system/)
