/**
 * 东八区（北京时间）统一时间格式化工具
 * 严格去除 'T'、'Z'、毫秒和时区偏移量，格式统一为: YYYY-MM-DD HH:mm:ss
 */
export function formatBeijingTime(dateInput?: string | number | Date | null): string {
  if (!dateInput) {
    return "-";
  }

  try {
    const d = typeof dateInput === "string" || typeof dateInput === "number" ? new Date(dateInput) : dateInput;
    if (isNaN(d.getTime())) {
      // 无法有效识别为 Date 对象时，对原始字符串进行降级清理
      return String(dateInput)
        .replace("T", " ")
        .replace(/\.\d+.*$/, "")
        .replace(/Z|(\+\d{2}:\d{2})$/, "")
        .trim();
    }

    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });

    const parts = formatter.formatToParts(d);
    const map: Record<string, string> = {};
    for (const p of parts) {
      map[p.type] = p.value;
    }

    return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute}:${map.second}`;
  } catch {
    return String(dateInput)
      .replace("T", " ")
      .replace(/Z|(\+\d{2}:\d{2})$/, "")
      .trim();
  }
}
