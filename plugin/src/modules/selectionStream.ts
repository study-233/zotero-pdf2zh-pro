/** Incremental SSE framing; decoding UTF-8 belongs to the network reader. */
export function createSelectionStreamParser(
    requestId: string,
    onDelta: (text: string) => void,
) {
    let buffer = "",
        sequence = 0,
        started = false,
        done = false;
    let result: unknown;
    return {
        feed(text: string) {
            buffer += text;
            if (buffer.length > 1024 * 1024) throw new Error("流式事件过长。");
            let boundary: RegExpExecArray | null;
            while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
                const frame = buffer.slice(0, boundary.index);
                buffer = buffer.slice(boundary.index + boundary[0].length);
                let event = "message";
                const data: string[] = [];
                for (const line of frame.split(/\r?\n/)) {
                    if (line.startsWith("event:")) event = line.slice(6).trim();
                    if (line.startsWith("data:"))
                        data.push(line.slice(5).replace(/^ /, ""));
                }
                if (!data.length) continue;
                const value = JSON.parse(data.join("\n"));
                if (value.requestId !== requestId) continue;
                if (
                    done ||
                    !Number.isInteger(value.seq) ||
                    value.seq <= sequence
                )
                    throw new Error("流式事件顺序无效。");
                sequence = value.seq;
                if (event === "start") {
                    if (started) throw new Error("重复的流式开始事件。");
                    started = true;
                } else if (!started) throw new Error("缺少流式开始事件。");
                else if (event === "delta") {
                    if (typeof value.text !== "string")
                        throw new Error("流式文本格式无效。");
                    onDelta(value.text);
                } else if (event === "done") {
                    result = value;
                    done = true;
                } else if (event === "error")
                    throw new Error(
                        typeof value.message === "string"
                            ? value.message
                            : "翻译未完成，请重试。",
                    );
            }
        },
        finish() {
            if (!done)
                throw new Error("连接提前结束，译文未完成。请手动重试。");
            return result;
        },
    };
}
