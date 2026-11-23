/** Incremental SSE decoding shared by all task execution modes. */
export async function consumeTaskEventStream(
  response: Response,
  onEvent: (event: Record<string, unknown>) => void,
): Promise<void> {
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(String(data.detail ?? `任务启动失败：${response.status}`));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let line = '';
  let dataLines: string[] = [];
  let skipLF = false;
  let complete = false;

  function finishLine() {
    if (!line) {
      if (dataLines.length) {
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(dataLines.join('\n'));
          if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error();
        } catch {
          throw new Error('执行消息格式异常，请到任务历史查看已保存的进度。');
        }
        dataLines = [];
        onEvent(event);
        complete = event.type === 'complete';
      }
    } else if (line === 'data' || line.startsWith('data:')) {
      const value = line === 'data' ? '' : line.slice(5);
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value);
    }
    line = '';
  }

  function consume(text: string) {
    for (const char of text) {
      if (complete) return;
      if (skipLF) {
        skipLF = false;
        if (char === '\n') continue;
      }
      if (char === '\r' || char === '\n') {
        finishLine();
        skipLF = char === '\r';
      } else {
        line += char;
      }
    }
  }

  try {
    while (!complete) {
      const {value, done} = await reader.read().catch(() => {
        throw new Error('执行连接已中断，后台任务可能仍在运行。请到任务历史刷新进度，避免重复提交。');
      });
      consume(done ? decoder.decode() : decoder.decode(value, {stream: true}));
      if (done) break;
    }
    if (!complete) throw new Error('执行连接已中断，后台任务可能仍在运行。请到任务历史刷新进度，避免重复提交。');
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
