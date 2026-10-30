import {
  BaselineIcon,
  BookTextIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  FolderIcon,
  HeadphonesIcon,
  HighlighterIcon,
  MinusIcon,
  MoveHorizontalIcon,
  PanelLeftIcon,
  PanelRightIcon,
  PenToolIcon,
  RefreshCwIcon,
  ScanIcon,
  SearchIcon,
  SquareIcon,
  StickyNoteIcon,
  SunIcon,
  TypeIcon,
  Undo2Icon,
  XIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from "lucide-react";
import { BASE } from "@/shared/lib/seo";
import styles from "./paper-reader.module.css";

const PAGE_COUNT = 15;

export function PaperReader() {
  return (
    <div className={styles.reader}>
      {/* Decorative app chrome; only the document below is interactive. */}
      <div className={styles.chrome} aria-hidden="true">
        <div className={styles.menuBar}>
          <img
            className={styles.logo}
            src={`${BASE}/site/zotero-reader/zotero-mark.png`}
            width={16}
            height={16}
            alt=""
          />
          <div className={styles.menuItems}>
            {["文件", "编辑", "查看", "前往", "工具", "帮助"].map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
          <div className={styles.windowControls}>
            <MinusIcon /><SquareIcon /><XIcon />
          </div>
        </div>
        <div className={styles.tabs}>
          <SunIcon />
          <span className={styles.library}><FolderIcon />我的文库</span>
          <div className={styles.tab}>
            <img
              className={styles.pdfLight}
              src={`${BASE}/site/zotero-reader/pdf-light.svg`}
              width={16}
              height={16}
              alt=""
            />
            <img
              className={styles.pdfDark}
              src={`${BASE}/site/zotero-reader/pdf-dark.svg`}
              width={16}
              height={16}
              alt=""
            />
            <span className={styles.tabTitle}>Attention Is All You Need</span>
            <XIcon />
          </div>
          <div className={styles.tabActions}><ChevronDownIcon /><RefreshCwIcon /></div>
        </div>
        <div className={styles.toolbar}>
          <div className={styles.toolGroup}><PanelLeftIcon /></div>
          <div className={styles.toolGroup}>
            <ZoomOutIcon /><ZoomInIcon /><MoveHorizontalIcon />
            <BookTextIcon className={styles.secondaryTool} />
            <HeadphonesIcon className={styles.secondaryTool} />
          </div>
          <div className={`${styles.toolGroup} ${styles.secondaryTool}`}><Undo2Icon /></div>
          <div className={`${styles.toolGroup} ${styles.pagination}`}>
            <ChevronUpIcon /><ChevronDownIcon />
            <span className={styles.pageInput}>1</span>
            <span>1 / {PAGE_COUNT}</span>
          </div>
          <div className={`${styles.toolGroup} ${styles.annotations}`}>
            <HighlighterIcon /><BaselineIcon /><StickyNoteIcon />
            <TypeIcon /><ScanIcon /><PenToolIcon />
          </div>
          <div className={styles.readingTools}>
            <span className={styles.versions}>双语对照<ChevronDownIcon /></span>
            <TypeIcon className={styles.secondaryTool} /><SearchIcon /><PanelRightIcon />
          </div>
        </div>
      </div>
      <div
        className={styles.viewport}
        role="region"
        aria-label="Attention Is All You Need 完整双语论文，15 页，可滚动阅读"
        tabIndex={0}
      >
        <div className={styles.pages}>
          {Array.from({ length: PAGE_COUNT }, (_, index) => (
            <div className={styles.page} key={index}>
              <img
                className={styles.paper}
                src={`${BASE}/assets/guide/attention-bilingual/page-${String(index + 1).padStart(2, "0")}.webp`}
                alt={`Attention Is All You Need 英中双语对照，第 ${index + 1} 页，共 ${PAGE_COUNT} 页`}
                width={2800}
                height={1812}
                loading={index === 0 ? "eager" : "lazy"}
                fetchPriority={index === 0 ? "high" : undefined}
                draggable={false}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
