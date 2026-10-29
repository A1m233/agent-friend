# 开源准备：第三方许可核对

核对日期：2026-10-29。源码基线：`635b81079d4ca9b6ae5b6aba3055b8ef9ea0e87e`，叠加本次隐私与 Apache-2.0 声明变更。本文记录证据和未决项，不构成权利方授权或完整发行合规结论。

| 对象 | 本次事实 | 判断边界 |
| --- | --- | --- |
| 自有代码 | 根 LICENSE、NOTICE 与包元信息为 Apache-2.0 | 不重授第三方权利 |
| bilibili-api-python 17.4.1 | 安装包元数据为 GPL-3.0-or-later；`bilibili.py` / `bilibili_auth.py` 直接导入；`scripts/package/dependency_policy.py` 标为 `spec-collect-all` | 不能把当前完整组合仅视为 Apache-2.0；保留并满足 GPL 分发条件、替换依赖或调整功能，需要另行决定 |
| bilibili-api 上游状态 | 2026-10-29 核实官方仓库已于 2026-07-06 归档，首页声明永久关停并展示平台方侵权告知函 | 停止维护与平台使用授权问题独立于 GPL；不能仅靠改许可证、换 fork 或自行重写来消除。告知函是上游展示的主张，不是对本项目的裁判结论 |
| easy-live2d 0.4.4 | 安装包为 MPL-2.0 | 文件级义务，与 Cubism Core 和模型授权分开处理 |
| pixi-spine / runtime-3.8 4.0.6 | 安装包声明 `SEE SPINE-LICENSE`；实际许可文本为 2020-01-01 版 Spine Runtimes License | 不是普通 MIT 包；需确认适用的集成和再分发许可 |
| spine-pixi-v8 4.3.9 | 安装包声明 `LicenseRef-LICENSE`；依赖 spine-canvas/core | 不能由 Apache-2.0 根许可证覆盖 |
| Live2D Core / Hiyori | 当前代码固定加载 Hiyori；用户目录扫描和导入只支持 Spine 3.8 | 不得仅由“有模型导入”推断已触发 Live2D 可扩展应用条款；具体使用方式仍须核对协议 |
| 明日方舟模型 | 源码带 14 套模型；`scripts/package/run.py` 将模型复制到 `avatar-seed/spine/ark-models` | “非商业探索”标签不会阻止实际进入安装包；上游素材收集者声明不替代权利方授权 |
| 评测截图 | 生产来源附件的个人关联标识已移除，像素仍是原始第三方视频页面 | 隐私整理不等于取得截图再分发授权 |
| 其他已识别许可 | certifi 为 MPL-2.0；tld 提供 MPL/GPL/LGPL 多许可选项；dompurify 为 MPL-2.0 OR Apache-2.0；lightningcss 为 MPL-2.0 | 开发依赖和产品运行依赖应分别列入最终制品清单，不能仅看包名出现就认定均随产品分发 |

## 来源

- [bilibili-api-python 17.4.1 发布元数据](https://pypi.org/project/bilibili-api-python/17.4.1/)：明确标为 GPL-3.0-or-later，与本地安装元数据一致。
- [bilibili-api 上游关停声明](https://github.com/Nemo2011/bilibili-api)：归档日期、停止维护声明与所展示的告知函；历史 PyPI 包仍可获得不代表上游仍受维护。
- [Apache 关于 GPL 兼容方向的说明](https://www.apache.org/licenses/GPL-compatibility.html)：Apache 源码可以进入符合 GPLv3 的组合，不能反向把 GPL 代码改授 Apache；是否形成衍生组合需要核对集成方式。
- [Spine 运行库协议](https://esotericsoftware.com/spine-runtimes-license)与[Editor 协议](https://esotericsoftware.com/spine-editor-license)：集成、产品分发及第三方修改条件；版本核对同时保留安装包自带文本。
- [Live2D 可扩展应用说明](https://www.live2d.com/en/sdk/license/expandable/)：规定相应类别的审批要求，但本项目尚不能只凭 Spine 导入功能归入该类别。
- [Live2D 样例条款](https://www.live2d.com/eula/live2d-sample-model-terms_en.html)与[素材许可](https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html)：Hiyori 的设计限制、使用范围与指定版权声明。
- [Ark-Models 版权声明](https://github.com/isHarryh/Ark-Models#版权声明)：版权归鹰角、非商业限制；未取得额外权利方授权证据。

## 尚需用户提供或决定

- 是否已有覆盖本项目的 Spine 集成许可，以及是否存在其他单独约定。
- 明日方舟模型和第三方截图是否已有公开再分发许可；没有时需决定替换或移出公开范围。
- B 站集成的处理方向。当前建议公开版移出 SDK 集成，后续按官方授权能力评估接回；尚未获得用户对功能调整的决定。仅添加第三方声明不能完成该项处理。

本轮未联系权利方、未购买许可、未移除产品能力，也未宣称当前安装包可以直接公开发布。

## B 站集成评估

本次本地代码核对：`BilibiliApi` 已提供独立接口，`BilibiliSdkApi` 实现集中在 `bilibili.py` 的 145–303 行；上层搜索、阅读、结果归一化不必整体推倒。SDK 接入覆盖搜索、视频详情与字幕、文章与动态、UP 主资料、作品列表及代表作。`bilibili_auth.py` 另依赖 SDK 的 Credential 类型、TV 渠道二维码登录与轮询。替换范围包含凭据边界、工厂装配、依赖锁文件和打包策略，不是删除一个 import。

| 方向 | 工程与发行影响 | 当前建议 |
| --- | --- | --- |
| 保留现有 SDK，组合按 GPL 条件发行 | 代码改动少，但需处理对应源码、许可及其他组合组件的兼容性；上游停止维护和平台使用授权问题仍在 | 不作为首选 |
| 换 fork 或重写非公开接口适配 | 既有接口可复用，但需承担请求认证、登录、接口变动等维护；fork 的历史 GPL 不能因改名字消失，重写也不自动获得平台授权 | 不建议为赶开源而采用 |
| 公开版移出 SDK 集成 | 取消 B 站专用搜索、阅读及扫码登录入口，并从依赖和制品中移除；保留通用平台契约与其他连接器，需同步工具描述和测试 | 当前推荐，等待用户确认功能范围 |
| 官方开放平台接入 | [官方能力目录](https://open.bilibili.com/doc)包含账号授权、授权用户数据和稿件管理；尚无证据证明它能等价覆盖本项目的全站搜索、任意视频字幕与任意 UP 主查询 | 取得应用权限后按实际能力另行评估，不承诺等价替换 |

自有源码继续采用 Apache-2.0 与评估 GPL 组合发行并不矛盾；限制针对组合的分发条件，不应描述成“使用 GPL 后所有独立代码自动变成 GPL”。将同一 SDK 搬到可选包、子进程或 MCP 也不是自动免责，仍需分析组合关系和平台使用授权。
