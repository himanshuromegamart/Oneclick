"""Populate the system with a realistic category tree and sample documents.

For demonstrating the app and for exercising a fresh deployment end to end -
the files really are uploaded to Cloudinary, so a successful run proves the
storage credentials, the database and the whole upload path all work.

    python manage.py seed_demo_data
    python manage.py seed_demo_data --owner-phone 9876543210 --owner-password "..."
    python manage.py seed_demo_data --clear

Safe to run twice: categories and files that already exist are left alone.
"""

from __future__ import annotations

import io
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.core.validators import normalize_phone_number
from apps.files.models import FileAsset
from apps.files.services import FileService
from apps.folders.models import Folder
from apps.folders.repositories import FolderRepository

# The tree. Nested dicts, so the shape here is the shape in the app.
CATEGORY_TREE: dict[str, Any] = {
    "Documents": {
        "Company Documents": {"GST": {}, "PAN": {}, "Aadhaar": {}},
        "Certificates": {"ISO": {}, "BIS": {}},
        "Agreements": {},
    },
    "Products": {
        "Water ATM": {"500 LPH": {}, "1000 LPH": {}, "Solar ATM": {}},
        "Water Cooler": {"40 Litre": {}, "80 Litre": {}, "Industrial": {}},
        "RO Plant": {},
    },
    "Price List": {},
    "Brochures & Catalogues": {},
    "Photos": {},
    "Installation & Warranty": {},
}

#: (category path, filename, kind, title, subtitle)
SAMPLE_FILES: list[tuple[list[str], str, str, str, str]] = [
    (
        ["Price List"],
        "Sarah-Aqua-Price-List-2026.pdf",
        "pdf",
        "Price List 2026",
        "Water ATM, Coolers and RO Plants",
    ),
    (
        ["Products", "Water ATM", "500 LPH"],
        "Water-ATM-500-LPH-Brochure.pdf",
        "pdf",
        "Water ATM - 500 LPH",
        "Specifications and installation notes",
    ),
    (
        ["Products", "Water Cooler", "40 Litre"],
        "Water-Cooler-40L-Specification.pdf",
        "pdf",
        "Water Cooler - 40 Litre",
        "Technical specification sheet",
    ),
    (
        ["Documents", "Certificates", "ISO"],
        "ISO-9001-Certificate.pdf",
        "pdf",
        "ISO 9001:2015",
        "Quality management certification",
    ),
    (
        ["Brochures & Catalogues"],
        "Sarah-Aqua-Product-Catalogue.pdf",
        "pdf",
        "Product Catalogue",
        "The complete range",
    ),
    (["Photos"], "water-atm-500lph.png", "image", "Water ATM 500 LPH", "#0B6FB5"),
    (["Photos"], "water-cooler-40l.png", "image", "Water Cooler 40L", "#0E8F6F"),
    (["Products", "RO Plant"], "ro-plant-overview.png", "image", "RO Plant", "#2A6FA8"),
]


def build_pdf(title: str, subtitle: str) -> io.BytesIO:
    """A small, real PDF - not a text file with a .pdf name."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setFillColorRGB(0.04, 0.44, 0.71)
    pdf.rect(0, height - 40 * mm, width, 40 * mm, fill=1, stroke=0)

    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(20 * mm, height - 22 * mm, "Sarah Aqua Soft")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(20 * mm, height - 31 * mm, "Water purification systems")

    pdf.setFillColorRGB(0.1, 0.1, 0.1)
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawString(20 * mm, height - 60 * mm, title)
    pdf.setFont("Helvetica", 11)
    pdf.setFillColorRGB(0.35, 0.35, 0.35)
    pdf.drawString(20 * mm, height - 69 * mm, subtitle)

    pdf.setFillColorRGB(0.2, 0.2, 0.2)
    pdf.setFont("Helvetica", 10)
    y = height - 88 * mm
    for line in [
        "This is sample content used to demonstrate the document manager.",
        "",
        "Replace it with the real document when the app goes into use.",
        "",
        "Model              Capacity        Notes",
        "Water ATM          500 LPH         Card and coin operated",
        "Water ATM        1000 LPH          High footfall locations",
        "Water Cooler        40 L           Stainless steel storage",
        "Water Cooler        80 L           Suitable for offices",
        "RO Plant          Custom           Sized to requirement",
    ]:
        pdf.drawString(20 * mm, y, line)
        y -= 6 * mm

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.setFillColorRGB(0.5, 0.5, 0.5)
    pdf.drawString(20 * mm, 15 * mm, "Sample document - Sarah Aqua Soft document manager")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


def build_image(title: str, hex_colour: str) -> io.BytesIO:
    """A labelled placeholder image, so thumbnails have something to show."""
    from PIL import Image, ImageDraw

    colour = tuple(int(hex_colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    image = Image.new("RGB", (1200, 800), colour)
    draw = ImageDraw.Draw(image)

    # A lighter panel so the text is legible without bundling a font file.
    lighter = tuple(min(c + 45, 255) for c in colour)
    draw.rectangle([60, 60, 1140, 740], outline=lighter, width=6)
    draw.text((100, 360), title, fill=(255, 255, 255))
    draw.text((100, 390), "Sarah Aqua Soft - sample image", fill=(235, 235, 235))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class Command(BaseCommand):
    help = "Create a demo category tree and upload sample documents."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--owner-phone",
            default="",
            help="Attribute the content to this user, creating them if needed.",
        )
        parser.add_argument(
            "--owner-password",
            default="",
            help="Password for the owner, when one is being created.",
        )
        parser.add_argument(
            "--categories-only",
            action="store_true",
            help="Build the tree but upload nothing (no Cloudinary calls).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove the seeded categories and their files, then stop.",
        )

    def handle(self, *args, **options) -> None:
        if options["clear"]:
            self._clear()
            return

        owner = self._resolve_owner(options)
        created_folders = self._seed_tree(owner)

        if options["categories_only"]:
            self.stdout.write(
                self.style.SUCCESS(f"Created {created_folders} categories. No files uploaded.")
            )
            return

        uploaded, skipped = self._seed_files(owner)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_folders} new categories, {uploaded} files uploaded"
                + (f", {skipped} already present." if skipped else ".")
            )
        )

    # -- owner ------------------------------------------------------------
    def _resolve_owner(self, options: dict) -> User:
        phone = options["owner_phone"]

        if phone:
            phone = normalize_phone_number(phone)
            user = User.all_objects.filter(phone_number=phone).first()
            if user is None:
                if not options["owner_password"]:
                    raise CommandError(
                        "No account for that number. Pass --owner-password to create one."
                    )
                user = User.objects.create_user(
                    phone_number=phone, full_name="Owner", role=UserRole.OWNER
                )
                user.set_password(options["owner_password"])
                user.save(update_fields=["password"])
                self.stdout.write(self.style.SUCCESS(f"Created owner {phone}."))
            return user

        owner = User.objects.filter(role=UserRole.OWNER).first() or User.objects.first()
        if owner is None:
            raise CommandError(
                "There are no users yet. Create one first, or pass "
                "--owner-phone and --owner-password."
            )
        return owner

    # -- categories -------------------------------------------------------
    def _seed_tree(self, owner: User) -> int:
        repo = FolderRepository()
        created = 0

        def walk(node: dict[str, Any], parent: Folder | None, depth: int) -> None:
            nonlocal created
            for name, children in node.items():
                folder = Folder.objects.filter(parent=parent, name=name).first()
                if folder is None:
                    folder = repo.create_folder(name=name, parent=parent, created_by=owner)
                    created += 1
                    self.stdout.write(f"{'  ' * depth}+ {name}")
                else:
                    self.stdout.write(f"{'  ' * depth}. {name} (exists)")
                walk(children, folder, depth + 1)

        self.stdout.write(self.style.MIGRATE_HEADING("Categories"))
        walk(CATEGORY_TREE, None, 0)
        return created

    # -- files ------------------------------------------------------------
    def _seed_files(self, owner: User) -> tuple[int, int]:
        service = FileService()
        uploaded = skipped = 0

        self.stdout.write(self.style.MIGRATE_HEADING("\nDocuments"))
        for path, filename, kind, title, extra in SAMPLE_FILES:
            folder = self._folder_at(path)
            if folder is None:
                self.stdout.write(self.style.WARNING(f"  ? {'/'.join(path)} missing, skipped"))
                continue

            if FileAsset.objects.filter(folder=folder, name=filename).exists():
                self.stdout.write(f"  . {filename} (exists)")
                skipped += 1
                continue

            payload = build_pdf(title, extra) if kind == "pdf" else build_image(title, extra)
            size = payload.getbuffer().nbytes

            try:
                service.upload(
                    owner,
                    folder_id=folder.pk,
                    file_obj=payload,
                    filename=filename,
                    size_bytes=size,
                    content_type="application/pdf" if kind == "pdf" else "image/png",
                    description=f"{title} - sample document",
                    tags=["sample", kind],
                )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  x {filename}: {exc}"))
                continue

            self.stdout.write(f"  + {filename}  ->  {'/'.join(path)}")
            uploaded += 1

        return uploaded, skipped

    @staticmethod
    def _folder_at(path: list[str]) -> Folder | None:
        parent = None
        for name in path:
            parent = Folder.objects.filter(parent=parent, name=name).first()
            if parent is None:
                return None
        return parent

    # -- teardown ---------------------------------------------------------
    @transaction.atomic
    def _clear(self) -> None:
        """Remove the seeded roots and everything beneath them.

        Cloudinary objects are removed too - leaving them would keep costing
        storage with nothing in the database pointing at them.
        """
        from apps.files.storage import get_storage_backend

        storage = get_storage_backend()
        removed_files = removed_folders = 0

        for name in CATEGORY_TREE:
            root = Folder.all_objects.filter(parent=None, name=name).first()
            if root is None:
                continue

            ids = [
                root.pk,
                *Folder.all_objects.filter(path__startswith=root.subtree_prefix).values_list(
                    "id", flat=True
                ),
            ]

            for asset in FileAsset.all_objects.filter(folder_id__in=ids):
                try:
                    storage.delete(asset.public_id, asset.resource_type)
                except Exception as exc:
                    # Report and carry on: a blob that will not delete must not
                    # leave the rest of the demo data stranded in the database.
                    self.stdout.write(self.style.WARNING(f"  could not remove {asset.name}: {exc}"))
                removed_files += 1

            from django.db.models.query import QuerySet

            QuerySet.delete(FileAsset.all_objects.filter(folder_id__in=ids))
            QuerySet.delete(Folder.all_objects.filter(pk__in=ids))
            removed_folders += len(ids)

        self.stdout.write(
            self.style.SUCCESS(f"Removed {removed_folders} categories and {removed_files} files.")
        )
