import csv
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from documents.models import Category, Document, PrintGroup


class Command(BaseCommand):
    help = 'Import documents from a CSV file exported from Google Sheets'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file',
            type=str,
            help='Path to the CSV file exported from Google Sheets'
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing documents instead of skipping them',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear all existing documents, categories, and print groups before importing',
        )

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']
        update_existing = options['update']
        clear_existing = options['clear']

        try:
            # Clear existing data if requested
            if clear_existing:
                self.stdout.write(self.style.WARNING('Clearing all existing documents, categories, and print groups...'))
                Document.objects.all().delete()
                Category.objects.all().delete()
                PrintGroup.objects.all().delete()
                self.stdout.write(self.style.SUCCESS('Cleared existing data'))

            # Read and parse CSV file
            # Read all rows into memory first so we can process them multiple times
            with open(csv_file_path, 'r', encoding='utf-8') as file:
                # Try to detect delimiter
                sample = file.read(1024)
                file.seek(0)
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(sample).delimiter
                
                reader = csv.DictReader(file, delimiter=delimiter)
                # Read all rows into memory
                all_rows = list(reader)
                fieldnames = reader.fieldnames
                
                # Get all column names
                if not fieldnames:
                    raise CommandError('CSV file appears to be empty or invalid')
                
                self.stdout.write(f'Found columns: {", ".join(fieldnames)}')
                
                # Identify column indices
                # Expected columns: Document (A), Description (B), Category (C), Print Group (D), then other print groups
                doc_col = None
                desc_col = None
                cat_col = None
                print_group_header_col = None  # The "Print Group" column header
                print_group_cols = []  # Other columns that are print groups (column headers)
                
                # Find column indices (case-insensitive)
                for i, col in enumerate(fieldnames):
                    col_lower = col.lower().strip()
                    if 'document' in col_lower and doc_col is None:
                        doc_col = i
                    elif 'description' in col_lower and desc_col is None:
                        desc_col = i
                    elif 'category' in col_lower and cat_col is None:
                        cat_col = i
                    elif 'print' in col_lower and 'group' in col_lower and print_group_header_col is None:
                        # This is the "Print Group" column header
                        print_group_header_col = i
                    elif col_lower and col_lower not in ['document', 'description', 'category']:
                        # All other columns are potential print groups
                        # Include even if header is empty (we'll use cell values as print group names)
                        print_group_cols.append(i)
                
                self.stdout.write(f'Document column: {fieldnames[doc_col] if doc_col is not None else "NOT FOUND"}')
                self.stdout.write(f'Description column: {fieldnames[desc_col] if desc_col is not None else "NOT FOUND"}')
                self.stdout.write(f'Category column: {fieldnames[cat_col] if cat_col is not None else "NOT FOUND"}')
                if print_group_header_col is not None:
                    self.stdout.write(f'Print Group column (header): {fieldnames[print_group_header_col]}')
                self.stdout.write(f'Print group columns (from headers): {len(print_group_cols)} found')
                if print_group_cols:
                    self.stdout.write(f'Print group column names: {", ".join([fieldnames[i] for i in print_group_cols])}')
                
                if doc_col is None or desc_col is None or cat_col is None:
                    raise CommandError('Required columns (Document, Description, Category) not found in CSV')
                
                # Initialize print groups dictionary
                all_print_groups = {}
                
                # Helper function to get or create print group
                def get_or_create_print_group(name):
                    """Get or create a print group and return it"""
                    name = name.strip()
                    if not name:
                        return None
                    if name not in all_print_groups:
                        print_group, created = PrintGroup.objects.get_or_create(
                            name=name,
                            defaults={'description': f'Print group: {name}'}
                        )
                        all_print_groups[name] = print_group
                        if created:
                            self.stdout.write(self.style.SUCCESS(f'  ✓ Created print group: "{name}"'))
                        else:
                            self.stdout.write(self.style.WARNING(f'  - Print group already exists: "{name}"'))
                    return all_print_groups[name]
                
                # First, create all print groups from column headers (before processing rows)
                # This ensures all print groups exist even if no documents use them yet
                self.stdout.write('\nCreating print groups from column headers...')
                
                # Create print groups from column headers (after Category)
                # Only create from headers that have names (not empty)
                for col_idx in print_group_cols:
                    col_name = fieldnames[col_idx].strip()
                    if col_name:  # Only create from non-empty headers
                        get_or_create_print_group(col_name)
                
                self.stdout.write(f'\nTotal print groups from headers: {len(all_print_groups)}')
                
                # First pass: Read all rows to collect ALL print groups from cell values
                # This ensures we create all print groups even if documents are skipped
                self.stdout.write('\nFirst pass: Collecting all print groups from cell values...')
                
                for row_num, row in enumerate(all_rows, start=2):
                    try:
                        # Get print groups from "Print Group" column (cell values, comma-separated)
                        if print_group_header_col is not None:
                            print_group_value = row[fieldnames[print_group_header_col]].strip() if row[fieldnames[print_group_header_col]] else None
                            if print_group_value:
                                pg_names = [pg.strip() for pg in print_group_value.split(',') if pg.strip()]
                                for pg_name in pg_names:
                                    get_or_create_print_group(pg_name)
                        
                        # Get print groups from other columns (cell values where header is empty)
                        for col_idx in print_group_cols:
                            col_name = fieldnames[col_idx].strip()
                            value = row[fieldnames[col_idx]].strip() if row[fieldnames[col_idx]] else None
                            
                            if value:  # Only process if cell has a value
                                if not col_name:
                                    # Column has no header (empty) - use the cell value as print group name
                                    get_or_create_print_group(value)
                    except Exception:
                        # Skip errors in first pass, we'll catch them in second pass
                        pass
                
                self.stdout.write(f'Total print groups after first pass: {len(all_print_groups)}')
                self.stdout.write('\nSecond pass: Processing documents...')
                
                # Second pass: Process rows to create/update documents
                created_count = 0
                updated_count = 0
                skipped_count = 0
                errors = []
                
                with transaction.atomic():
                    for row_num, row in enumerate(all_rows, start=2):  # Start at 2 because row 1 is header
                        try:
                            # Get document name and description
                            doc_name = row[fieldnames[doc_col]].strip() if row[fieldnames[doc_col]] else None
                            doc_desc = row[fieldnames[desc_col]].strip() if row[fieldnames[desc_col]] else None
                            category_name = row[fieldnames[cat_col]].strip() if row[fieldnames[cat_col]] else None
                            
                            # Skip empty rows
                            if not doc_name or not category_name:
                                skipped_count += 1
                                continue
                            
                            # Get or create category
                            category, _ = Category.objects.get_or_create(
                                name=category_name,
                                defaults={'description': f'Category for {category_name} documents'}
                            )
                            
                            # Collect print groups for this document
                            document_print_groups = []
                            
                            # 1. Get print groups from "Print Group" column (cell values, comma-separated)
                            if print_group_header_col is not None:
                                print_group_value = row[fieldnames[print_group_header_col]].strip() if row[fieldnames[print_group_header_col]] else None
                                if print_group_value:
                                    # Split by comma if multiple print groups
                                    pg_names = [pg.strip() for pg in print_group_value.split(',') if pg.strip()]
                                    for pg_name in pg_names:
                                        pg = get_or_create_print_group(pg_name)
                                        if pg and pg not in document_print_groups:
                                            document_print_groups.append(pg)
                            
                            # 2. Get print groups from other columns
                            # Strategy:
                            # - If column has a header name -> use header as print group name (if cell has value)
                            # - If column has no header (empty) but has a value -> use the value as print group name
                            for col_idx in print_group_cols:
                                col_name = fieldnames[col_idx].strip()
                                value = row[fieldnames[col_idx]].strip() if row[fieldnames[col_idx]] else None
                                
                                if value:  # Only process if cell has a value
                                    if col_name:
                                        # Column has a header - use header as print group name
                                        pg = get_or_create_print_group(col_name)
                                        if pg and pg not in document_print_groups:
                                            document_print_groups.append(pg)
                                    else:
                                        # Column has no header (empty) - use the cell value as print group name
                                        pg = get_or_create_print_group(value)
                                        if pg and pg not in document_print_groups:
                                            document_print_groups.append(pg)
                            
                            # Create or update document
                            defaults = {
                                'description': doc_desc or '',
                                'category': category,
                            }
                            
                            if update_existing:
                                document, created = Document.objects.update_or_create(
                                    name=doc_name,
                                    defaults=defaults
                                )
                                # Set print groups (many-to-many)
                                document.print_groups.set(document_print_groups)
                                
                                if created:
                                    created_count += 1
                                else:
                                    updated_count += 1
                            else:
                                document, created = Document.objects.get_or_create(
                                    name=doc_name,
                                    defaults=defaults
                                )
                                # Set print groups (many-to-many)
                                document.print_groups.set(document_print_groups)
                                
                                if created:
                                    created_count += 1
                                else:
                                    skipped_count += 1
                                    self.stdout.write(
                                        self.style.WARNING(f'Row {row_num}: Document "{doc_name}" already exists, skipping')
                                    )
                        
                        except Exception as e:
                            error_msg = f'Row {row_num}: Error processing - {str(e)}'
                            errors.append(error_msg)
                            self.stdout.write(self.style.ERROR(error_msg))

                from documents.account_library import (
                    sync_all_master_documents_to_all_libraries,
                    sync_all_master_print_groups_to_all_libraries,
                )

                sync_all_master_documents_to_all_libraries()
                sync_all_master_print_groups_to_all_libraries()
                self.stdout.write(
                    self.style.SUCCESS(
                        "Synced catalog documents and print groups to all subaccount libraries (idempotent)."
                    )
                )
                
                # Summary
                self.stdout.write(self.style.SUCCESS('\n' + '='*50))
                self.stdout.write(self.style.SUCCESS('Import Summary:'))
                self.stdout.write(self.style.SUCCESS(f'  Documents created: {created_count}'))
                if update_existing:
                    self.stdout.write(self.style.SUCCESS(f'  Documents updated: {updated_count}'))
                else:
                    self.stdout.write(self.style.SUCCESS(f'  Documents skipped (existing): {skipped_count}'))
                self.stdout.write(self.style.SUCCESS(f'  Total print groups: {PrintGroup.objects.count()}'))
                self.stdout.write(self.style.SUCCESS(f'  Errors: {len(errors)}'))
                
                if errors:
                    self.stdout.write(self.style.ERROR('\nErrors encountered:'))
                    for error in errors[:10]:  # Show first 10 errors
                        self.stdout.write(self.style.ERROR(f'  - {error}'))
                    if len(errors) > 10:
                        self.stdout.write(self.style.ERROR(f'  ... and {len(errors) - 10} more errors'))
        
        except FileNotFoundError:
            raise CommandError(f'CSV file not found: {csv_file_path}')
        except Exception as e:
            raise CommandError(f'Error importing documents: {str(e)}')
