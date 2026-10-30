import Link from "next/link";
export default function NotFound() {
  return (
    <main className="site-container py-24">
      <p className="text-sm text-muted-foreground">404</p>
      <h1 className="mt-4 text-4xl font-semibold">页面没有找到</h1>
      <Link href="/" className="mt-8 inline-block underline underline-offset-4">
        返回首页
      </Link>
    </main>
  );
}
