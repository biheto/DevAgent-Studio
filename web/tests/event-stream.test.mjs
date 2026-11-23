import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Use the project's existing compiler; no test framework or browser dependency needed.
const source = await readFile(new URL('../src/event-stream.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {compilerOptions: {target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext}});
const { consumeTaskEventStream } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

function responseFor(text, chunkSize = 1) {
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) return controller.close();
      controller.enqueue(bytes.slice(offset, offset + chunkSize));
      offset += chunkSize;
    },
  }));
}

for (const ending of ['\n', '\r\n', '\r']) {
  test(`decodes UTF-8, comments and multiline data with ${JSON.stringify(ending)}`, async () => {
    const events = [];
    const text = [': heartbeat', '', 'event: message', 'data:{"type":"task",', 'data: "content":"中文 🚀"}', '', 'data: {"type":"complete"}', '', ''].join(ending);
    const response = responseFor(text);
    await consumeTaskEventStream(response, event => events.push(event));
    assert.deepEqual(events, [{type: 'task', content: '中文 🚀'}, {type: 'complete'}]);
    assert.equal(response.body.locked, false);
  });
}

test('handles many events in a single network chunk', async () => {
  const events = [];
  await consumeTaskEventStream(responseFor('data: {"type":"task"}\n\ndata: {"type":"task_result"}\n\ndata: {"type":"complete"}\n\n', 2048), event => events.push(event));
  assert.equal(events.length, 3);
});

test('detects premature EOF even after receiving a partial result', async () => {
  for (const text of ['', 'data: {"type":"task"}\n\n', 'data: {"type":"task_result"}\n\n', 'data: {"type":"complete"}']) {
    const response = responseFor(text);
    await assert.rejects(consumeTaskEventStream(response, () => {}), /连接已中断/);
    assert.equal(response.body.locked, false);
  }
});

test('reports malformed events and releases the reader', async () => {
  for (const value of ['{broken', 'null', '[]', '42']) {
    const response = responseFor(`data: ${value}\n\n`);
    await assert.rejects(consumeTaskEventStream(response, () => {}), /消息格式异常/);
    assert.equal(response.body.locked, false);
  }
});

test('network failure offers history recovery rather than silently succeeding', async () => {
  const response = new Response(new ReadableStream({
    pull(controller) { controller.error(new TypeError('network failure')); },
  }));
  await assert.rejects(consumeTaskEventStream(response, () => {}), /任务历史/);
  assert.equal(response.body.locked, false);
});

test('surfaces server validation failures without starting the reader', async () => {
  await assert.rejects(consumeTaskEventStream(new Response(JSON.stringify({detail: 'Invalid workflow'}), {status: 400}), () => {}), /Invalid workflow/);
});

test('cancels delivery once the completion marker arrives', async () => {
  let cancelled = false;
  const response = new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: {"type":"complete"}\n\n')); },
    cancel() { cancelled = true; },
  }));
  await consumeTaskEventStream(response, () => {});
  assert.equal(cancelled, true);
});
