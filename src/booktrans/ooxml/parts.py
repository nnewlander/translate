from collections.abc import Iterable
import zipfile

from lxml import etree

from booktrans.ooxml.package import is_xml_part


def xml_part_names(package: zipfile.ZipFile) -> Iterable[str]:
    return (name for name in package.namelist() if is_xml_part(name))


def read_xml_part(package: zipfile.ZipFile, name: str) -> etree._Element | None:
    """Parse a part when it is XML; malformed optional parts are diagnosed by the caller."""
    if not is_xml_part(name):
        return None
    return etree.fromstring(package.read(name))
