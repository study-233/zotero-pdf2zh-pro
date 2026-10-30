import { BenchmarkExplorer } from "@/features/benchmark/benchmark-explorer";
import { OpenHashDetails } from "@/shared/ui/open-hash-details";
import benchmark from "../../../../.generated-data/benchmark.json";
import "@/features/benchmark/benchmark.css";
export const metadata = {
  title: "模型测评",
  description: "比较论文翻译的质量、速度和费用，查看原始测评数据。",
};
export default function Benchmark() {
  return (
    <section className="site-container py-12 sm:py-16">
      <OpenHashDetails />
      <h1 className="font-serif text-4xl font-semibold sm:text-5xl">
        模型测评
      </h1>
      <p className="mt-5 mb-10 text-lg leading-8 text-muted-foreground">
        质量、速度、费用，一起比较。
      </p>
      <BenchmarkExplorer html={benchmark.html} />
    </section>
  );
}
