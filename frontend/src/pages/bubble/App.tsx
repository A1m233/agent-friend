import { useEffect } from "react";
import { PetBubble } from "@/components/pet/PetBubble";
import { startPetBubbleSubscriber } from "@/stores/petBubble";
import { startNotificationSubscriber } from "@/stores/notifications";

/**
 * 016 · 桌宠气泡独立窗（label="bubble"）入口。
 *
 * 本入口保留通知账本与 legacy push 订阅。PetBubble 的呈现 owner 统一处理
 * 动画、尺寸和原生窗口显隐；退出节点完成动画前，不按 store 条数提前隐藏窗口。
 * 提醒卡片使用项目主题 token，窗口剩余区域保持透明。
 */
export function BubbleApp() {
  useEffect(() => {
    const stopNotifications = startNotificationSubscriber();
    let unlistenPush: (() => void) | null = null;
    void startPetBubbleSubscriber().then((u) => {
      unlistenPush = u;
    });
    return () => {
      stopNotifications();
      unlistenPush?.();
    };
  }, []);

  return (
    <div className="h-full w-full overflow-hidden bg-transparent">
      <PetBubble />
    </div>
  );
}
