# Document Import Commands

## Import Documents from CSV

This command imports documents from a CSV file exported from Google Sheets.

### Exporting from Google Sheets

1. Open your Google Sheets document
2. Go to **File** → **Download** → **Comma-separated values (.csv)**
3. Save the CSV file to your project directory

### Usage

Basic import (skips existing documents):
```bash
python manage.py import_documents path/to/your/file.csv
```

Update existing documents:
```bash
python manage.py import_documents path/to/your/file.csv --update
```

Clear all existing data before importing:
```bash
python manage.py import_documents path/to/your/file.csv --clear
```

### CSV Format

The CSV file should have the following columns:
- **Document** (Column A): Document name
- **Description** (Column B): Document description
- **Category** (Column C): Document category
- **Print Group columns** (Columns D onwards): Each column header represents a print group name (e.g., "Profit & Loss", "Conventional Refinance (W-2)", "FHA Refinance (W-2)"). If a document belongs to that print group, the cell should have a value (any value).

### Example CSV Structure

```csv
Document,Description,Category,Profit & Loss,Conventional Refinance (W-2),FHA Refinance (W-2),VA Refinance (W-2)
Bank Statements (Last 2 months),Bank statements for all accounts from the last 2 months,Assets,Yes,Yes,Yes,Yes
Gift Letter,Written statement confirming funds being used for down payment,Assets,,Yes,Yes,Yes
```

### How It Works

1. **Categories**: Created automatically if they don't exist
2. **Print Groups**: Each column header (after Category) becomes a PrintGroup record if it doesn't exist
3. **Documents**: Created with relationships to Category and PrintGroups
4. **Relationships**: If a cell has a value, that document is linked to the print group represented by that column header

### Notes

- The command automatically detects column positions
- Empty rows are skipped
- Categories are created automatically if they don't exist
- Print groups are created automatically from column headers
- Each print group column that has a value for a document creates a relationship between that document and print group
- You can query documents by print group: `PrintGroup.objects.get(name="Profit & Loss").documents.all()`

