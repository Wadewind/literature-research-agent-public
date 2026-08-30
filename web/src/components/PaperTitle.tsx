import type { PaperListItem } from "../api/types";
import { paperDisplayTitle } from "../library/paperCatalog";

interface PaperTitleProps {
  paper: PaperListItem;
  className?: string;
}

export default function PaperTitle({ paper, className = "" }: PaperTitleProps) {
  const title = paperDisplayTitle(paper);
  const classes = [
    "paper-title",
    paper.title ? "" : "paper-title-fallback",
    className,
  ].filter(Boolean).join(" ");

  return <span className={classes} title={title}>{title}</span>;
}
