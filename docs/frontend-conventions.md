# 前端视觉规范：工业监控桌面 UI

> 阶段 2-B2 补齐写入。项目为 Windows 桌面端 PySide6 应用；当前无现有前端代码可继承。本规范采用通用工业监控风格：高信息密度、低装饰、强状态识别、报警优先。

## 1. 风格基调

- **工业监控优先**：界面以数据可读性、报警可见性和现场排障效率为第一目标。
- **冷静中性底色**：亮色主题用于日常值守；暗色主题用于大屏/夜间值守。
- **克制动效**：仅报警、焦点、加载使用动效；禁止装饰性大面积渐变和复杂动画。

## 2. 设计 Token

### 2.1 基础色板

| Token | 值 | 用途 |
| --- | --- | --- |
| `--color-slate-950` | `#0B1220` | 暗色背景 |
| `--color-slate-900` | `#111827` | 暗色面板 |
| `--color-slate-800` | `#1F2937` | 暗色边框/二级面 |
| `--color-slate-700` | `#334155` | 次级文字暗色 |
| `--color-slate-100` | `#F1F5F9` | 亮色背景 |
| `--color-slate-50` | `#F8FAFC` | 亮色页面底 |
| `--color-white` | `#FFFFFF` | 亮色面板 |
| `--color-blue-700` | `#1D4ED8` | 主操作/运行中 |
| `--color-blue-600` | `#2563EB` | 主操作 hover |
| `--color-cyan-500` | `#06B6D4` | 数据强调/在线信号 |
| `--color-green-600` | `#16A34A` | 正常/成功 |
| `--color-amber-500` | `#F59E0B` | 预警/维护提醒 |
| `--color-orange-600` | `#EA580C` | 低报/超限前置 |
| `--color-red-600` | `#DC2626` | 高报/危险/删除 |
| `--color-red-700` | `#B91C1C` | 报警闪烁深色 |
| `--color-gray-500` | `#6B7280` | 离线/禁用 |

### 2.2 语义色

| Token | 亮色 | 暗色 | 用途 |
| --- | --- | --- | --- |
| `--bg-window` | `#F1F5F9` | `#0B1220` | 主窗口背景 |
| `--bg-panel` | `#FFFFFF` | `#111827` | 卡片/表格/面板 |
| `--bg-subtle` | `#E2E8F0` | `#1F2937` | 次级区域 |
| `--text-primary` | `#0F172A` | `#F8FAFC` | 主文字 |
| `--text-secondary` | `#475569` | `#CBD5E1` | 辅助文字 |
| `--border-default` | `#CBD5E1` | `#334155` | 默认边框 |
| `--status-normal` | `#16A34A` | `#22C55E` | 正常 |
| `--status-running` | `#1D4ED8` | `#60A5FA` | 运行中 |
| `--status-warning` | `#F59E0B` | `#FBBF24` | 预警/维护 |
| `--status-low-alarm` | `#EA580C` | `#FB923C` | 低报 |
| `--status-high-alarm` | `#DC2626` | `#F87171` | 高报/危险 |
| `--status-fault` | `#B91C1C` | `#FCA5A5` | 故障 |
| `--status-offline` | `#6B7280` | `#94A3B8` | 离线 |
| `--status-shielded` | `#7C3AED` | `#A78BFA` | 屏蔽 |
| `--status-warmup` | `#0891B2` | `#22D3EE` | 预热 |

### 2.3 字号与字体

| Token | 值 | 用途 |
| --- | --- | --- |
| `--font-family-ui` | `Microsoft YaHei UI, Segoe UI, Arial, sans-serif` | Windows 桌面 UI |
| `--font-family-mono` | `Consolas, Cascadia Mono, monospace` | HEX、寄存器、日志 |
| `--font-size-xs` | `11px` | 表格辅助信息 |
| `--font-size-sm` | `12px` | 标签、状态 |
| `--font-size-md` | `14px` | 正文/表单 |
| `--font-size-lg` | `16px` | 卡片标题 |
| `--font-size-xl` | `20px` | 页面标题 |
| `--font-size-2xl` | `24px` | 监控大数字 |
| `--font-size-screen-title` | `36px` | 大屏标题 |
| `--font-size-screen-metric` | `56px` | 大屏核心数值 |
| `--font-weight-regular` | `400` | 正文 |
| `--font-weight-medium` | `500` | 标签/按钮 |
| `--font-weight-bold` | `700` | 报警、大屏数值 |

### 2.4 间距、圆角、边框

| Token | 值 | 用途 |
| --- | --- | --- |
| `--space-1` | `4px` | 紧密间距 |
| `--space-2` | `8px` | 表单内部 |
| `--space-3` | `12px` | 控件间距 |
| `--space-4` | `16px` | 卡片内边距 |
| `--space-6` | `24px` | 页面分区 |
| `--space-8` | `32px` | 页面外边距 |
| `--radius-sm` | `4px` | 状态徽标/输入框 |
| `--radius-md` | `6px` | 按钮/卡片 |
| `--radius-lg` | `10px` | 大屏面板 |
| `--border-width` | `1px` | 常规边框 |
| `--focus-width` | `2px` | 焦点描边 |

## 3. Qt/QSS Token 建议

QSS 不支持 CSS 变量，应用层应在 Python 中维护 token 字典并格式化 QSS。示例：

```python
LIGHT_TOKENS = {
    "bg_window": "#F1F5F9",
    "bg_panel": "#FFFFFF",
    "text_primary": "#0F172A",
    "text_secondary": "#475569",
    "border_default": "#CBD5E1",
    "primary": "#1D4ED8",
    "primary_hover": "#2563EB",
    "danger": "#DC2626",
    "warning": "#F59E0B",
    "normal": "#16A34A",
    "offline": "#6B7280",
    "radius_md": "6px",
    "font_ui": "Microsoft YaHei UI",
}
```

基础 QSS 片段：

```css
QWidget {
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
    color: #0F172A;
    background: #F1F5F9;
}
QFrame[panel="true"] {
    background: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
}
QPushButton[variant="primary"] {
    min-height: 32px;
    padding: 0 16px;
    color: #FFFFFF;
    background: #1D4ED8;
    border: 1px solid #1D4ED8;
    border-radius: 6px;
    font-weight: 500;
}
QPushButton[variant="primary"]:hover { background: #2563EB; }
QPushButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border-color: #CBD5E1;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 2px solid #1D4ED8;
}
```

## 4. 状态表达规则

| 状态 | 颜色 | 图标/形态 | 文案规则 |
| --- | --- | --- | --- |
| 正常 | `--status-normal` | 实心圆点 | “正常” |
| 运行中 | `--status-running` | 脉冲圆点 | “采集中/运行中” |
| 重连中 | `--status-warning` | 旋转/进度 | “重连中，第 N 次” |
| 低报 | `--status-low-alarm` | 橙色闪烁边框 | “低报：值 单位” |
| 高报 | `--status-high-alarm` | 红色闪烁背景+边框 | “高报：值 单位” |
| 故障 | `--status-fault` | 红色边框+故障图标 | “故障：原因” |
| 离线 | `--status-offline` | 灰色置灰 | “离线，最后更新时间” |
| 屏蔽 | `--status-shielded` | 紫色锁/盾牌 | “已屏蔽” |
| 预热 | `--status-warmup` | 青色时钟 | “预热中” |

## 5. 报警闪烁规范

- 闪烁只用于当前未恢复报警、故障和超量程；恢复后立即停止。
- 周期：`800ms`；红色状态在 `#DC2626` 与 `#B91C1C` 之间切换。
- 地图点位闪烁半径：常规点位 `12px`，报警外圈 `22px`。
- 避免全屏大面积闪烁；大屏只让报警卡片、点位和顶部警情条闪烁。

PySide6 建议：使用 `QTimer` 每 `400ms` 切换动态属性 `alarmPulse=true/false` 后调用 `style().unpolish/polish`。

```python
def set_alarm_pulse(widget, enabled: bool) -> None:
    widget.setProperty("alarmPulse", enabled)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
```

```css
QFrame[alarm="high"][alarmPulse="true"] {
    background: #FEE2E2;
    border: 2px solid #DC2626;
}
QFrame[alarm="high"][alarmPulse="false"] {
    background: #FFFFFF;
    border: 2px solid #B91C1C;
}
```

## 6. 大屏字体与布局

- 大屏默认暗色主题，背景 `#0B1220`，面板 `#111827`。
- 顶部标题 `36px/700`，核心指标 `56px/700`，报警文本 `24px/700`，列表文字 `18px/500`。
- 大屏边距：外边距 `32px`，面板间距 `24px`，面板内边距 `24px`。
- 无报警时按轮播展示；有报警时顶部警情条占高 `72px` 并优先展示报警地图或设备状态。

## 7. 权限与安全输出 UI 规则

- 权限不足：按钮禁用并显示锁图标；用户主动点击受限入口时显示受控提示：“当前账号无权限执行此操作，已记录权限失败事件。”
- 不在 UI 中展示密码、授权码、完整机器标识、数据库绝对路径、密钥、堆栈。
- 授权失败只显示“授权校验失败，请联系管理员或供应商”，不得显示算法细节。
- 设备调试 HEX 可显示原始收发帧，但单条最大显示 `2048` 字符，超出折叠为“已截断”。
- 备份恢复错误只显示结构校验、文件类型、采集未停止等业务原因，不显示内部路径穿越细节。
- API 端口占用提示：“本地 API 启动失败：端口被占用。桌面监控不受影响。”

## 8. 减法检查结论

- 不使用装饰性插画作为监控页面核心视觉。
- 不使用紫蓝渐变 SaaS 风格作为主视觉。
- 不使用复杂玻璃拟态和大面积阴影，避免降低工业现场可读性。
- 仅保留状态色、边框、点位闪烁、数据字号层级作为主要视觉语言。
