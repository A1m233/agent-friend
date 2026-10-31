---
name: pokemon-firered
description: 在 AF 中独立游玩已配置的中文版宝可梦火红，观察画面、批量按键、有界推进对话、记录探索并保存。用户要求开始、继续、暂停火红时使用。需要本地游戏适配器；不负责直播、下载 ROM 或安装系统软件。
---

# 自主游玩宝可梦火红

从获准起点独立推进，以击败冠军并进入名人堂为目标。遵守本轮时限；没有预算先确认。只根据画面与自己的探索记录决策，不读 RAM、攻略，不使用金手指或另起模型玩家。

## 开始与图片回传

绑定 Skill 的 exec_command 使用 cmd，不能同时传 argv。绑定当前可信 skill_id，entry 为 scripts/game.py；不猜安装路径，不用 shell 控制符或 python -c。脚本使用 AF 受管 Python 已提供的 Pillow。

先执行一次 status：

```json
{"cmd":"python scripts/game.py status","skill":{"skill_id":"<当前可信 Skill ID>","entry":"scripts/game.py"},"access":{"filesystem":"read_write","network":"disabled"},"timeout_seconds":30}
```

它返回 paused、pending、instance_root、screens_root。后续操作：

1. 为命令 access.additional_roots 添加 instance_root 的 read_write 范围，仍由宿主授权。
2. 用 request_filesystem_access 为 screens_root 申请目录级 read_image scope，保存可信 filesystem_scope_id；同一轮复用，不重复申请。
3. 用户明确开始或恢复后执行 begin。begin、observe、act、wait、dialogue、checkpoint、finish 均添加 `output_images: {"filesystem_scope_id":"<图片 scope>"}`。脚本输出 image_paths，宿主在同次工具结果中返回原始图片；直接根据这些图片继续，无需再调用 view_image。
4. status、pause、resume 没有图片，不传 output_images。pending 非空先 observe 对账，不能重放旧操作。

output_images 只用于 buffered 命令。若命令已执行但图片回传失败，不重新执行动作；使用已有 scope 和结果中的 image_paths 单独 view_image。若宿主未支持此字段，说明需更新并重启 dev，不能声称已直接看图。

## 移动、交互与验证

所有操作使用最近实际看过的最终画面的 observation_id。例子为接在 python scripts/game.py 后的简单参数：

- `act --observation-id <编号> --tap A`：按住 8 帧，松开 24 帧。
- `act --observation-id <编号> --step RIGHT:16 --expect 靠近门口`：有序执行并记录预期；expect 最多 200 字，含空格时正确引用。
- `--step B+RIGHT:30` 表示组合键；WAIT 表示松开。tap 与 step 互斥，混合动作全部用 step 表达。

每批输入最多 32 段/600 帧。act 自动等待画面稳定；普通画面总计最多 600 帧。遇到蓝底战斗文字后，只追加无按键等待，总计最多 1200 帧，仍受单次 20 秒/8 图及本轮截止时间约束；到战斗菜单、确认箭头或其它边界立即返回，并保留中途稳定文字页。visual.settled 只表示画面稳定，不等于战斗结算完；decision_ready / stop_reason 给出停止原因，原图仍是判断依据。battle_confirmation 需要你看图决定如何确认；frame_budget、unstable、macro_time_limit 或 text_unchanged 不能视为已就绪；text_unchanged 只是有文字的蓝底页面约 8 秒未变，可能在等确认也可能仍有动画，先看末图决定等待或确认，不能机械按 A。入场插画不按普通稳定场景返回。若仍是转场或自动战斗动画，使用 `wait --observation-id <编号>`，普通画面最多 600 帧，蓝底战斗最多 1200 帧，均最多 20 秒且不按任何键。不要用 A 来代替等待。动作结果已有后续画面，不额外 observe。

正常移动只返回当前图；画面几乎不变或重复近期画面时自动附前图。需要精确对照的方向操作可加 `--compare`。comparison_included=true 时首图是操作前；末图始终是当前，只用末图的 observation_id。before_image 保留原图文件名供必要时恢复查看。先核对角色相对树、台阶、门的位置，再判断是否实现 expect。visual.motion 只报告粗略画面变化/背景滚动，不提供角色坐标，也不证明碰撞或可通行。little_screen_change 时检查是否被挡住或动作过短，不直接重复。

visual.recent_attempts 只记录尝试，不是成功路线；repeat_matches_recent 容忍少量地图动画，但不证明未执行。未知路口先单方向 8–16 帧验证，确认缺口后再走较长距离；不要在未确认通路时组合左右上下。失败两次后用一句话记录当前地标、已试方向和下一项不同验证，防止左右反复猜测。菜单和战斗也按当前可见状态短步操作。

靠近 NPC 或柜台后，先核对位置和朝向。转向与 A 之间留出松键等待，例如 `--step LEFT:8 --step WAIT:24 --step A:8`；不要把尚未走完的长移动、转向和 A 紧接在一起。交互未触发时先确认朝向，不原样重复 A；移动中的角色未必已经接受后续转向。

## 有界推进普通对话

蓝底战斗文字明确出现确认箭头，且你决定继续时，优先调用 `confirm-text --observation-id <编号>`（与 dialogue 一样传 output_images）。它逐页截图，只在识别到蓝底确认箭头时先等待去除箭头闪动后的整幅画面连续 60 帧稳定，再短按 A 8 帧并松开 24 帧，最多 7 次；菜单、选项、普通对白、未知布局、重复页面或没有确认箭头的停滞文字都会停止，绝不替你选招式、回答命名或接受其它选择。图片按时间排列，必须读完全部页面；末图为当前。仍受输入 600 帧、被动战斗等待 1200 帧、20 秒、8 图和本轮截止时间约束。不传 tap/step。不确定或没有箭头时先看图决定 wait 或手动确认，不能把这个命令当作通用连按 A。

普通白底对白支持当前蓝框和灰框路牌两种样式，仍要核对原图。

当前明确是普通对白时，优先调用 `dialogue --observation-id <编号>`，不必逐页调用 act。harness 会重新观察、等待文字区域稳定、保存页面，然后逐次轻按 B 并松开；每次采样优先检查额外选择框，检测到后只无按键等待说明文字稳定；即使窗口随后消失，本批也停止，不继续按 B。普通对白最多 600 帧/20 秒/8 张返回图。不传 tap 或 step。转入蓝底战斗文字后仅无按键等待，总帧上限改为 1200，20 秒/8 图和本轮截止时间不变；不再按 B，战斗菜单和确认文字交还你决定，不能假定 dialogue 会替你确认。

图片按时间排列，最后一张为当前画面，前面是本批对话页。阅读整批内容后再规划，不能只看最后一张。图片有本地原件，批量截图不代表无限保留历史上下文。

- dialogue_closed_or_unknown：对白已关闭或当前布局无法识别；根据最后一张图决定下一步。
- choice_or_overlay：本批检测到额外窗口/可能的选项，当前状态以末图为准，必须由你观察选择；不机械续按 B。
- battle_menu / battle_confirmation：已到战斗菜单或文字确认边界，阅读末图后自行选择；不会自动按 B。
- repeated_page：同一页再次出现，停止重复推进，核对是不是重新触发了 NPC 对话。
- unstable：文字/转场尚未稳定；先核对画面，不盲目确认。
- text_unchanged：蓝底文字持续未变；这是不确定性提示，不是确认箭头或选择许可，由你看图决定等待还是确认。
- image_budget / frame_budget：本批达到上限，读完图片再决定是否继续。

识别器适配当前火红白色对白框、蓝底战斗文字和底部战斗菜单；原图仍是决策依据。招式、行动、学习技能、进化、命名、购买及其它选择必须由你操作，不能调用 dialogue 代选；未知皮肤也交还你。B 在菜单中是取消键。普通对话结束后长串 A 可能重新触发 NPC，勿这样跳对话。OCR 已离线试验但未用于控制，返回值不含 OCR 识别结果。

## 保存、暂停与恢复

budget_remaining_seconds 是本轮余量。提前约一分钟 finish，不能延长或另起后台任务。finish 成功后直接简短汇报已核实的进度和存档，不再调用工具翻查旧结果；未核实的奖励细节明确说明未知，留待下一次获准游玩时检查。finish 合并 checkpoint 与 pause，失败也请求暂停；checkpoint 只保存不暂停，两者都不要求手填观察编号。

快照仅覆盖模拟器，不等于 AF 上下文已配对恢复。用户只要求暂停时直接 pause。stale_observation 先 observe 并看图；action_unknown 保持暂停并对账，不重放；controller_busy 等待原控制者结束；ROM、图片、回执或资源不匹配则停止报告。

看到冠军胜利和名人堂后保存截图与 checkpoint，报告待核验。没有真实画面及有效存档证据不能宣称通关；本轮结束后不得声称仍在后台续玩。
