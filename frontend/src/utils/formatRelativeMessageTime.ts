import dayjs from "dayjs";
import type { ConfigType } from "dayjs";
import "dayjs/locale/zh-cn";

dayjs.locale("zh-cn");

/**
 * IM 消息时间展示规则：
 * - 今天：HH:mm
 * - 昨天：昨天 HH:mm
 * - 近 7 天：星期几 HH:mm
 * - 今年更早：M月D日 HH:mm
 * - 跨年：YYYY年M月D日 HH:mm
 */
export function formatRelativeMessageTime(input: ConfigType, nowInput: ConfigType = new Date()): string {
  const time = dayjs(input);
  const now = dayjs(nowInput);
  if (!time.isValid() || !now.isValid()) return "";

  const dayDiff = now.startOf("day").diff(time.startOf("day"), "day");
  if (dayDiff === 0) return time.format("HH:mm");
  if (dayDiff === 1) return time.format("[昨天] HH:mm");
  if (dayDiff > 1 && dayDiff < 7) return time.format("dddd HH:mm");
  if (time.isSame(now, "year")) return time.format("M月D日 HH:mm");
  return time.format("YYYY年M月D日 HH:mm");
}
