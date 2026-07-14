import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu } from "lucide-react";
import { Sidebar } from "./sidebar";
import { IntegrityBanner } from "@/components/shared/integrity-banner";
import { EntryDetailDialog } from "@/components/shared/entry-detail-dialog";
import { CommandPalette } from "@/components/shared/command-palette";
import { RouteErrorBoundary } from "@/components/shared/error-boundary";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop sidebar: >=lg */}
      <div className="hidden lg:flex">
        <Sidebar />
      </div>

      <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-[38px] lg:py-[40px]">
        {/* Mobile nav button */}
        <div className="mb-4 flex items-center gap-2 lg:hidden">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" aria-label="Open navigation">
                <Menu className="h-4 w-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <div onClick={() => setMobileOpen(false)}>
                <Sidebar />
              </div>
            </SheetContent>
          </Sheet>
          <span className="font-mono text-xs font-bold tracking-widest text-muted-foreground">
            KYDE · AUDIT
          </span>
        </div>

        <IntegrityBanner />
        <RouteErrorBoundary>
          <Outlet />
        </RouteErrorBoundary>
      </main>

      <EntryDetailDialog />
      <CommandPalette />
    </div>
  );
}
