import type { DictionarySense } from "./selectionDictionaryStore";

export type ContextMeaning = {
    pos?: string;
    meaning: string;
    explanation: string;
};

const partsOfSpeech: Record<string, string> = {
    "N-COUNT": "n. [C]",
    "N-UNCOUNT": "n. [U]",
    "N-VAR": "n. [C/U]",
    "N-PLURAL": "n. [pl.]",
    "N-SING": "n. [sing.]",
    NOUN: "n.",
    N: "n.",
    名词: "n.",
    可数名词: "n. [C]",
    不可数名词: "n. [U]",
    VERB: "v.",
    V: "v.",
    动词: "v.",
    "V-T": "vt.",
    VT: "vt.",
    及物动词: "vt.",
    "V-I": "vi.",
    VI: "vi.",
    不及物动词: "vi.",
    "V-T/V-I": "vt./vi.",
    ADJECTIVE: "adj.",
    ADJ: "adj.",
    A: "adj.",
    形容词: "adj.",
    ADVERB: "adv.",
    ADV: "adv.",
    AD: "adv.",
    副词: "adv.",
    PRON: "pron.",
    代词: "pron.",
    PREP: "prep.",
    介词: "prep.",
    CONJ: "conj.",
    连词: "conj.",
    INTERJ: "interj.",
    感叹词: "interj.",
    DET: "det.",
    限定词: "det.",
    NUM: "num.",
    数词: "num.",
    PHRASALVERB: "phr. v.",
    "PHRASAL VERB": "phr. v.",
    短语动词: "phr. v.",
    短语: "phr.",
};

function knownPartOfSpeech(key: string): string | undefined {
    return Object.prototype.hasOwnProperty.call(partsOfSpeech, key)
        ? partsOfSpeech[key]
        : undefined;
}

/** Known labels only: retain unfamiliar grammatical information verbatim. */
export function formatPartOfSpeech(value?: string): string {
    const label = value?.trim().replace(/\s+/g, " ") || "";
    const key = label.toUpperCase().replace(/\.$/, "");
    return knownPartOfSpeech(key) || label;
}

export function formatDefinition(value: string): string {
    return value
        .trim()
        .replace(/[ \t]+/g, " ")
        .replace(/\s*[;；]\s*/g, "；");
}

/** Parse only explicit POS prefixes; unrecognized lines stay intact. */
export function dictionarySenses(value: {
    senses?: DictionarySense[];
    text: string;
    pos?: string;
    aiGenerated?: boolean;
}): DictionarySense[] {
    const senses = value.senses?.length
        ? value.senses
        : value.text
              .split(/\r?\n/)
              .filter((line) => line.trim())
              .map((line) => {
                  const match = line.trim().match(/^(\S+)\s+(.+)$/);
                  const key = match?.[1].toUpperCase().replace(/\.$/, "");
                  return {
                      pos:
                          value.pos ||
                          (key && knownPartOfSpeech(key) ? match![1] : ""),
                      chinese:
                          !value.pos && key && knownPartOfSpeech(key)
                              ? match![2]
                              : line,
                      examples: [],
                  };
              });
    const normalized = senses.map((sense) => ({
        ...sense,
        pos: formatPartOfSpeech(sense.pos),
        chinese: formatDefinition(sense.chinese),
    }));
    // Only suppress completely identical AI senses, never edit imported entries.
    return value.aiGenerated
        ? normalized.filter(
              (sense, i) =>
                  normalized.findIndex(
                      (other) =>
                          JSON.stringify(other) === JSON.stringify(sense),
                  ) === i,
          )
        : normalized;
}

export function dictionaryCopyText(
    headword: string,
    phonetic: string | undefined,
    senses: DictionarySense[],
    usage?: string,
): string {
    return [
        headword,
        phonetic ? `/${phonetic.replace(/^\/+|\/+$/g, "")}/` : "",
        ...senses.map((sense, i) =>
            [
                `${i + 1}. ${[sense.pos, sense.chinese].filter(Boolean).join(" ")}`,
                sense.english,
                ...sense.examples.map(
                    (example) => `${example.english}\n${example.chinese}`,
                ),
            ]
                .filter(Boolean)
                .join("\n"),
        ),
        usage ? `用法说明：${usage}` : "",
    ]
        .filter(Boolean)
        .join("\n");
}

export function checkedContextMeaning(value: unknown): ContextMeaning {
    if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("本地服务返回的语境格式不正确。");
    const data = value as Record<string, unknown>;
    for (const key of ["meaning", "explanation", "pos"]) {
        if (key === "pos" && data[key] === undefined) continue;
        if (
            typeof data[key] !== "string" ||
            !data[key].trim() ||
            data[key].length > (key === "pos" ? 80 : 4000)
        )
            throw new Error("本地服务返回的语境格式不正确。");
    }
    return {
        ...(data.pos ? { pos: formatPartOfSpeech(data.pos as string) } : {}),
        meaning: formatDefinition(data.meaning as string),
        explanation: (data.explanation as string).trim(),
    };
}

export function contextCopyText(value: ContextMeaning): string {
    return [
        [value.pos, value.meaning].filter(Boolean).join(" "),
        value.explanation,
    ].join("\n");
}
