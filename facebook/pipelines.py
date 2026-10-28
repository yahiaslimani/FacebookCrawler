"""Item pipelines."""

import json

from itemadapter import ItemAdapter


class FacebookPipeline:
    """Append each item to a JSON Lines file as soon as it is scraped.

    The feed exporter buffers writes, so an interrupted run loses the
    tail. This one flushes per item and pairs with the spider's
    resume_file argument. Enabled by passing -a out_file=path.
    """

    def __init__(self):
        self.handle = None

    def open_spider(self, spider):
        """Open the output file when the spider is given one."""
        path = getattr(spider, "out_file", None)
        if path:
            # Held open for the whole run, so no context manager.
            # pylint: disable-next=consider-using-with
            self.handle = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def close_spider(self, spider):  # pylint: disable=unused-argument
        """Close the output file."""
        if self.handle:
            self.handle.close()

    def process_item(self, item, spider):  # pylint: disable=unused-argument
        """Write the item, then pass it on unchanged."""
        if self.handle:
            # asdict also converts the nested comment items.
            row = ItemAdapter(item).asdict()
            self.handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.handle.flush()
        return item
