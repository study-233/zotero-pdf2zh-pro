import { SiteHeader } from "@/features/marketing/components/site-header";
import { Footer } from "@/features/marketing/components/footer";
export default function SiteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="skip-link">
        跳到正文
      </a>
      <SiteHeader />
      <main id="main" className="flex-1">
        {children}
      </main>
      <Footer />
    </div>
  );
}
