# Third-party notices and distribution scope

Original agent-friend code and documentation remain available under Apache-2.0
in [LICENSE](LICENSE). Third-party materials retain their own licenses. This
file does not grant permission to relicense third-party code, models or media.

## Bilibili integration

The project retains `bilibili-api-python` 17.4.1 and its existing functionality.
The package is distributed under **GPL-3.0-or-later**, as declared in its
[versioned release metadata](https://pypi.org/project/bilibili-api-python/17.4.1/).
The GPL version 3 text copied verbatim from that package is provided in
[licenses/GPL-3.0.txt](licenses/GPL-3.0.txt). The upstream project identifies
MoyuScript and Nemo2011 as maintainers; their rights and notices are retained.

The Python engine directly imports the package, and the desktop packaging policy
collects it into the Python runtime. A distribution of that combined engine is
to be provided under the applicable GPLv3-or-later conditions, while original
Apache-2.0 source files retain their notices. The root Apache license does not
provide permission to distribute that combination under Apache terms alone.

Before distributing such a combination, its distributor must preserve the
applicable notices and licenses and provide the complete corresponding source
and build/install materials required by the GPL for the exact released version.
A link to the current repository or a dependency homepage alone is not a
verification of those obligations. No complete binary distribution source bundle
has been prepared as part of this source cleanup.

The upstream repository [announced closure](https://github.com/Nemo2011/bilibili-api)
in July 2026. Its software license does not grant authorization to use the
Bilibili platform or resolve the platform-related claims shown by upstream.

## Other components

The following index lists identified runtime, model, media and vendored-code
notices. It is not an exhaustive dependency inventory.

| 材料 | 许可与来源入口 |
| --- | --- |
| 内置第三方 Skills | [来源清单](agent/src/agent/skills/builtin_assets/manifest.json)及各 Skill 目录的许可证 |
| Vendored Tauri runtime | [Apache-2.0](frontend/src-tauri/vendor/tauri-runtime-wry/LICENSE_APACHE-2.0) / [MIT](frontend/src-tauri/vendor/tauri-runtime-wry/LICENSE_MIT) |
| 火山引擎语音 demo | [BSD-3-Clause 许可](experiments/voice-poc/rtc-aigc-demo/LICENSE) |
| Live2D Cubism Core | [专有软件许可入口](frontend/public/live2d-models/CUBISM_CORE_LICENSE.md) |
| Hiyori 样例模型 | [素材及样例模型许可入口](frontend/public/live2d-models/hiyori/LICENSE.md) |
| 明日方舟模型及测试样例 | [模型 NOTICE](frontend/public/spine-models/ark-models/NOTICE.md)、[测试样例 NOTICE](frontend/public/spine-spike-fixtures/ark-amiya/NOTICE.md) |
| Spine 运行库依赖 | 所使用版本随附的许可证及 [Spine 官方许可](https://esotericsoftware.com/spine-runtimes-license) |
| B 站连接器依赖 | 保留 `bilibili-api-python 17.4.1`：GPL-3.0-or-later；[许可全文](licenses/GPL-3.0.txt)与[组合分发说明](#bilibili-integration)；[上游已宣布关停](https://github.com/Nemo2011/bilibili-api) |
| Live2D 封装库 | `easy-live2d 0.4.4`：MPL-2.0；与 Cubism Core、样例模型的许可分别适用 |

MPL components require their applicable source and notice obligations to be met.
Live2D Core and Spine Runtimes retain their own terms; model and media rights
are separate from the engine's software licenses.

The Python engine, desktop client and bundled resources require a concrete
combination/aggregation review. Their placement in separate processes or in one
installer alone does not settle GPL scope. Compatibility and redistribution
permission for the complete desktop product remain unverified; this file is
not a declaration that every bundled component may be distributed under GPL.
