import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shared/page-header";

export default function NotFoundPage() {
  return (
    <>
      <PageHeader title="Not found" description="The page you requested doesn't exist." />
      <Button asChild variant="outline" size="sm"><Link to="/">Back to Overview</Link></Button>
    </>
  );
}
