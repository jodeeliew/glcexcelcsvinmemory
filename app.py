import streamlit as st
import pandas as pd
import sqlite3
import io
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
import re
import openpyxl
from sqlalchemy import create_engine, text
import tempfile
import json
import hashlib

load_dotenv()


class DatabaseManager:
    def __init__(self):
        # Create in-memory SQLite database with thread safety
        self.engine = create_engine(
            'sqlite:///:memory:',
            echo=False,
            poolclass=None,
            connect_args={'check_same_thread': False}
        )
        self.tables_info = {}
        self._connection = None

    def get_connection(self):
        """Get or create a database connection"""
        try:
            if self._connection is None or self._connection.closed:
                self._connection = self.engine.connect()
            return self._connection
        except:
            self._connection = self.engine.connect()
            return self._connection

    def load_csv_file(self, file_bytes, filename):
        """Load CSV file into database"""
        try:
            # Read CSV with pandas
            df = pd.read_csv(io.StringIO(file_bytes.decode('utf-8')))

            # Clean column names (remove spaces, special chars)
            original_columns = df.columns.tolist()
            df.columns = [self._clean_column_name(col) for col in df.columns]

            # Convert datetime and time columns to strings
            df = self._convert_datetime_columns(df)

            # Generate table name from filename
            table_name = self._clean_table_name(filename)

            # Load into SQLite
            df.to_sql(table_name, self.engine,
                      if_exists='replace', index=False)

            # Store table info with original column names for reference
            self.tables_info[table_name] = {
                'filename': filename,
                'columns': list(df.columns),  # Cleaned column names
                'original_columns': original_columns,  # Original column names
                'dtypes': df.dtypes.to_dict(),
                'shape': df.shape,
                'sample_data': df.head(3).to_dict('records')
            }

            # Verify table was created
            self._verify_table_creation(table_name)

            return table_name, df.shape

        except Exception as e:
            st.error(f"Detailed error loading CSV {filename}: {str(e)}")
            raise Exception(f"Error loading CSV {filename}: {str(e)}")

    def load_excel_file(self, file_bytes, filename):
        """Load Excel file into database (all sheets)"""
        try:
            # Read Excel file
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            loaded_tables = []

            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(excel_file, sheet_name=sheet_name)

                # Skip empty sheets
                if df.empty:
                    continue

                # Clean column names
                original_columns = df.columns.tolist()
                df.columns = [self._clean_column_name(
                    col) for col in df.columns]

                # Convert datetime and time columns to strings
                df = self._convert_datetime_columns(df)

                # Generate table name
                base_name = self._clean_table_name(filename)
                if len(excel_file.sheet_names) > 1:
                    table_name = f"{base_name}_{self._clean_table_name(sheet_name)}"
                else:
                    table_name = base_name

                # Load into SQLite
                df.to_sql(table_name, self.engine,
                          if_exists='replace', index=False)

                # Store table info
                self.tables_info[table_name] = {
                    'filename': filename,
                    'sheet_name': sheet_name,
                    'columns': list(df.columns),  # Cleaned column names
                    'original_columns': original_columns,  # Original column names
                    'dtypes': df.dtypes.to_dict(),
                    'shape': df.shape,
                    'sample_data': df.head(3).to_dict('records')
                }

                # Verify table was created
                self._verify_table_creation(table_name)

                loaded_tables.append((table_name, df.shape))

            return loaded_tables

        except Exception as e:
            st.error(f"Detailed error loading Excel {filename}: {str(e)}")
            raise Exception(f"Error loading Excel {filename}: {str(e)}")

    def _convert_datetime_columns(self, df):
        """Convert datetime and time columns to strings for SQLite compatibility"""
        import datetime

        for col in df.columns:
            try:
                # Check if column contains datetime.time, datetime.datetime, or pandas datetime objects
                if df[col].dtype == 'object':
                    # Sample a few non-null values to check the type
                    sample_values = df[col].dropna()

                if len(sample_values) > 0:
                    first_val = sample_values.iloc[0]

                    # Check if it's a time object
                    if isinstance(first_val, datetime.time):
                        df[col] = df[col].apply(
                            lambda x: str(x) if pd.notnull(x) else None)
                        continue

                    # Check if it's a datetime object
                    if isinstance(first_val, datetime.datetime):
                        df[col] = df[col].apply(
                            lambda x: str(x) if pd.notnull(x) else None)
                        continue

            # Handle pandas datetime columns
                elif pd.api.types.is_datetime64_any_dtype(df[col]):
                    df[col] = df[col].dt.strftime(
                        '%Y-%m-%d %H:%M:%S').where(df[col].notnull(), None)

            # Handle pandas time columns (if any exist)
                elif hasattr(df[col].dtype, 'name') and 'time' in str(df[col].dtype).lower():
                    df[col] = df[col].astype(str).where(
                        df[col].notnull(), None)

            except Exception as e:
                # If there's any error processing this column, skip it and continue
                print(f"Warning: Could not process column '{col}': {str(e)}")
            continue

        return df

    def _verify_table_creation(self, table_name):
        """Verify that a table was actually created in the database"""
        try:
            connection = self.get_connection()
            result = connection.execute(
                text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.fetchone()[0]
            st.sidebar.success(
                f"✓ Table '{table_name}' created with {count} rows")
        except Exception as e:
            st.sidebar.error(
                f"✗ Failed to verify table '{table_name}': {str(e)}")
            raise

    def _clean_column_name(self, col_name):
        """Clean column name for SQL compatibility"""
        # Convert to string and strip whitespace
        clean_name = str(col_name).strip()
        # Replace spaces and special characters with underscores
        clean_name = re.sub(r'[^\w]', '_', clean_name)
        # Remove multiple underscores
        clean_name = re.sub(r'_+', '_', clean_name)
        # Remove leading/trailing underscores
        clean_name = clean_name.strip('_')
        # Ensure it doesn't start with a number
        if clean_name and clean_name[0].isdigit():
            clean_name = f"col_{clean_name}"
        return clean_name or "unnamed_column"

    def _clean_table_name(self, filename):
        """Generate clean table name from filename"""
        # Remove file extension
        name = os.path.splitext(filename)[0]
        # Clean the name
        clean_name = re.sub(r'[^\w]', '_', name)
        clean_name = re.sub(r'_+', '_', clean_name)
        clean_name = clean_name.strip('_').lower()

        # Ensure uniqueness
        base_name = clean_name or "data_table"
        table_name = base_name
        counter = 1

        while table_name in self.tables_info:
            table_name = f"{base_name}_{counter}"
            counter += 1

        return table_name

    def execute_query(self, query):
        """Execute SQL query and return results"""
        try:
            connection = self.get_connection()
            result = connection.execute(text(query))
            columns = list(result.keys())
            rows = result.fetchall()
            return columns, rows
        except Exception as e:
            # Try to reconnect and retry once
            try:
                self._connection = None
                connection = self.get_connection()
                result = connection.execute(text(query))
                columns = list(result.keys())
                rows = result.fetchall()
                return columns, rows
            except Exception as retry_error:
                raise Exception(f"SQL Error: {str(retry_error)}")

    def close_connection(self):
        """Close database connection"""
        try:
            if self._connection and not self._connection.closed:
                self._connection.close()
        except:
            pass
        self._connection = None

    def get_schema_info(self):
        """Get database schema information for the LLM"""
        schema_info = []
        for table_name, info in self.tables_info.items():
            schema_info.append({
                'table_name': table_name,
                'filename': info['filename'],
                'columns': info['columns'],
                'original_columns': info.get('original_columns', info['columns']),
                'shape': info['shape'],
                'sample_data': info['sample_data'][:2]  # Limit sample data
            })
        return schema_info

    def validate_query_tables(self, query):
        """Validate that tables referenced in query exist"""
        # First, get available tables from our stored info (more reliable)
        available_tables = list(self.tables_info.keys())

        # Also try to get from database as backup
        try:
            connection = self.get_connection()
            result = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table';"))
            db_tables = [row[0] for row in result.fetchall()]
            # Use database tables if available, otherwise use stored info
            if db_tables:
                available_tables = db_tables
        except Exception as e:
            st.sidebar.warning(
                f"Could not query database tables directly: {str(e)}")

        # Extract table names from query (simple pattern matching)
        # Look for patterns like "FROM table_name" or "JOIN table_name"
        table_patterns = re.findall(
            r'(?:FROM|JOIN)\s+(\w+)', query, re.IGNORECASE)

        missing_tables = []
        for table in table_patterns:
            if table not in available_tables:
                missing_tables.append(table)

        if missing_tables:
            return False, f"Tables not found: {missing_tables}. Available tables: {available_tables}"

        return True, "Query validation passed"

    def get_all_table_names(self):
        """Get list of all table names in the database"""
        # Primary method: use stored table info
        stored_tables = list(self.tables_info.keys())

        # Secondary method: query database directly
        try:
            connection = self.get_connection()
            result = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table';"))
            db_tables = [row[0] for row in result.fetchall()]

            # Return database tables if available and non-empty, otherwise stored tables
            if db_tables:
                return db_tables
            else:
                return stored_tables

        except Exception as e:
            st.sidebar.warning(f"Error querying database tables: {str(e)}")
            return stored_tables


class QueryGenerator:
    def __init__(self):
        self.llm = ChatOpenAI(model_name="gpt-3.5-turbo", temperature=0)
        self.chat_history = []

    def generate_query(self, question, schema_info):
        """Generate SQL query from natural language question"""
        try:
            # Format schema info for the prompt
            schema_text = self._format_schema_info(schema_info)
            chat_history_text = self._format_chat_history()

            # Create the full prompt
            full_prompt = f"""You are a SQL expert. Generate ONLY the SQL query based on the user's question and database schema.

Database Schema Information:
{schema_text}

Chat History:
{chat_history_text}

CRITICAL RULES:
1. Generate ONLY valid SQLite SQL queries
2. Use the EXACT table and column names shown in the schema above
3. Do not modify, abbreviate, or change table names in any way
4. For aggregations (sum, count, average), be thorough and check ALL relevant data
5. Use appropriate WHERE clauses when filtering
6. Return ONLY the SQL query, no explanations or comments
7. Use proper SQL syntax (SELECT, FROM, WHERE, GROUP BY, ORDER BY, etc.)
8. For text searches, use LIKE with wildcards when appropriate
9. Handle case-insensitive searches when relevant using LOWER()
10. Table names are case-sensitive - use them exactly as shown
11. Column names shown are already cleaned for SQL - use them as-is

User Question: {question}

SQL Query:"""

            # Generate query using the LLM directly
            response = self.llm.invoke(full_prompt)

            # Extract content from the response
            if hasattr(response, 'content'):
                query_text = response.content
            else:
                query_text = str(response)

            # Clean the response to extract just the SQL query
            query = self._extract_sql_query(query_text)

            # Add to chat history
            self.chat_history.append({"question": question, "query": query})

            return query

        except Exception as e:
            raise Exception(f"Error generating query: {str(e)}")

    def _format_schema_info(self, schema_info):
        """Format schema information for the LLM prompt"""
        if not schema_info:
            return "No tables available"

        formatted = []
        for table in schema_info:
            # Show both original and cleaned column names for context
            col_mapping = ""
            if 'original_columns' in table and table['original_columns'] != table['columns']:
                col_mapping = "\nColumn mapping (Original → SQL):\n"
            for orig, clean in zip(table['original_columns'], table['columns']):
                if orig != clean:
                    col_mapping += f"  '{orig}' → {clean}\n"

        # Convert sample data to JSON-serializable format
        sample_data_serializable = self._make_json_serializable(
            table['sample_data'])

        table_info = f"""
Table: {table['table_name']} (from file: {table['filename']})
SQL Columns: {', '.join(table['columns'])}{col_mapping}
Rows: {table['shape'][0]}, Columns: {table['shape'][1]}
Sample data: {json.dumps(sample_data_serializable, indent=2, default=str)}

IMPORTANT: Use the EXACT table name "{table['table_name']}" and the SQL column names shown above in your queries.
"""
        formatted.append(table_info)
        return '\n'.join(formatted)

    def _make_json_serializable(self, data):
        """Convert data to JSON serializable format"""
        import pandas as pd
        import datetime

        if isinstance(data, list):
            return [self._make_json_serializable(item) for item in data]
        elif isinstance(data, dict):
            return {key: self._make_json_serializable(value) for key, value in data.items()}
        elif isinstance(data, (pd.Timestamp, datetime.datetime, datetime.date, datetime.time)):
            return str(data)
        elif pd.isna(data):
            return None
        elif isinstance(data, (int, float, str, bool, type(None))):
            return data
        else:
            # For any other type, convert to string
            return str(data)

    def _format_chat_history(self):
        """Format chat history for context"""
        if not self.chat_history:
            return "No previous conversation"

        history = []
        for item in self.chat_history[-3:]:  # Last 3 exchanges
            history.append(f"User: {item['question']}")
            history.append(f"Generated Query: {item['query']}")

        return '\n'.join(history)

    def _extract_sql_query(self, response):
        """Extract SQL query from LLM response"""
        # Remove code block markers
        query = re.sub(r'```sql\s*', '', response)
        query = re.sub(r'```\s*', '', query)

        # Remove explanations and keep only the query
        lines = query.strip().split('\n')
        sql_lines = []

        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('--'):
                sql_lines.append(line)

        return ' '.join(sql_lines).strip()


def get_file_hash(file_bytes, filename):
    """Generate a hash for the file to track changes"""
    content_hash = hashlib.md5(file_bytes).hexdigest()
    return f"{filename}_{content_hash}"


def validate_file_content(file_bytes, filename):
    """Validate file content matches its extension"""
    try:
        filename_lower = filename.lower()

        if filename_lower.endswith('.csv'):
            # Try to read as CSV
            pd.read_csv(io.StringIO(file_bytes.decode('utf-8')), nrows=1)
            return True, "Valid CSV file"

        elif filename_lower.endswith(('.xlsx', '.xls')):
            # Try to read as Excel
            pd.read_excel(io.BytesIO(file_bytes), nrows=1)
            return True, "Valid Excel file"

        else:
            return False, f"Unsupported file type: {filename}"

    except UnicodeDecodeError:
        return False, f"File encoding issue - {filename} may not be a valid text/CSV file"
    except Exception as e:
        return False, f"File validation failed for {filename}: {str(e)}"


def process_uploaded_files(uploaded_files, db_manager):
    """Process uploaded files automatically with improved error handling"""
    if not uploaded_files:
        return []

    # Create file hashes to track what's been processed
    current_file_hashes = []
    unsupported_files = []
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        filename = uploaded_file.name
        filename_lower = filename.lower()

        # Check file size
        if len(file_bytes) > MAX_FILE_SIZE:
            st.sidebar.error(
                f"❌ File too large: {filename} ({len(file_bytes)/1024/1024:.1f}MB). Max size: 50MB")
            continue

        # Check if file type is supported
        if not (filename_lower.endswith('.csv') or filename_lower.endswith(('.xlsx', '.xls'))):
            unsupported_files.append(filename)
            continue

        # Validate file content
        is_valid, validation_msg = validate_file_content(file_bytes, filename)
        if not is_valid:
            st.sidebar.error(f"❌ {validation_msg}")
            continue

        file_hash = get_file_hash(file_bytes, filename)
        current_file_hashes.append((file_hash, filename, file_bytes))

    # Show warning for unsupported files
    if unsupported_files:
        st.sidebar.error(
            f"❌ Unsupported file types detected: {', '.join(unsupported_files)}")
        st.sidebar.info("📝 Supported formats: CSV (.csv), Excel (.xlsx, .xls)")

    # If no supported files, return early
    if not current_file_hashes:
        if unsupported_files:
            st.sidebar.warning("No supported files to process!")
        return []

    # Check if files have changed
    if "last_file_hashes" not in st.session_state:
        st.session_state.last_file_hashes = []

    # Compare current files with previously processed files
    current_hashes = [h[0] for h in current_file_hashes]
    if current_hashes == st.session_state.last_file_hashes:
        return st.session_state.get("processed_files", [])

    # Files have changed, process them
    with st.spinner("🔄 Processing uploaded files..."):
        try:
            # Close existing connection and reset database for new upload
            db_manager.close_connection()
            st.session_state.db_manager = DatabaseManager()
            db_manager = st.session_state.db_manager
            loaded_files = []
            failed_files = []

            for file_hash, filename, file_bytes in current_file_hashes:
                try:
                    if filename.lower().endswith('.csv'):
                        table_name, shape = db_manager.load_csv_file(
                            file_bytes, filename
                        )
                        loaded_files.append(
                            f"✓ {filename} → {table_name} ({shape[0]} rows, {shape[1]} cols)")

                    elif filename.lower().endswith(('.xlsx', '.xls')):
                        tables = db_manager.load_excel_file(
                            file_bytes, filename
                        )
                        for table_name, shape in tables:
                            loaded_files.append(
                                f"✓ {filename} → {table_name} ({shape[0]} rows, {shape[1]} cols)")

                except Exception as file_error:
                    failed_files.append(f"❌ {filename}: {str(file_error)}")
                    st.sidebar.error(
                        f"Failed to process {filename}: {str(file_error)}")

            # Update session state
            st.session_state.processed_files = loaded_files
            st.session_state.last_file_hashes = current_hashes

            # Clear chat history when new data is loaded
            st.session_state.chat_history = []
            st.session_state.query_generator.chat_history = []

            # Show success message
            if loaded_files:
                st.sidebar.success(
                    f"Successfully processed {len([f for f in loaded_files if f.startswith('✓')])} file(s)!")

            # Show failed files summary
            if failed_files:
                st.sidebar.error(
                    f"Failed to process {len(failed_files)} file(s)")

            return loaded_files

        except Exception as e:
            st.sidebar.error(f"Error processing files: {str(e)}")
            return []


def handle_user_question(question, db_manager, query_generator):
    """Handle user question and generate response"""
    try:
        # Get schema information
        schema_info = db_manager.get_schema_info()

        if not schema_info:
            return "Please upload data files first.", None, None

        # Generate SQL query
        with st.spinner("🤖 Generating SQL query..."):
            sql_query = query_generator.generate_query(question, schema_info)

        # Validate query before execution
        is_valid, validation_msg = db_manager.validate_query_tables(sql_query)
        if not is_valid:
            return f"Query validation failed: {validation_msg}", sql_query, None

        # Execute query
        with st.spinner("🔍 Executing query..."):
            columns, rows = db_manager.execute_query(sql_query)

        # Format results and create more informative response
        if rows:
            df_result = pd.DataFrame(rows, columns=columns)

            # Create a more informative response message
            response_parts = [
                f"Query executed successfully! "]

            # If it's a simple aggregation query (like COUNT, SUM, AVG), show the result value
            if len(rows) == 1 and len(columns) == 1:
                result_value = rows[0][0]
                column_name = columns[0]
                response_parts.append(
                    f"**Result: {column_name} = {result_value}**")
            elif len(rows) == 1 and len(columns) <= 3:
                # Show results for simple queries with few columns
                result_summary = []
                for i, col in enumerate(columns):
                    result_summary.append(f"{col}: {rows[0][i]}")
                response_parts.append(
                    f"**Results: {', '.join(result_summary)}**")
            elif len(rows) <= 5:
                # For small result sets, show a preview
                response_parts.append("**Preview of results shown below.**")
            else:
                # For larger result sets
                response_parts.append(
                    f"**Showing all {len(rows)} results below.**")

            return " ".join(response_parts), sql_query, df_result
        else:
            return "Query executed successfully but returned no results.", sql_query, None

    except Exception as e:
        # Enhanced error message with available tables
        available_tables = db_manager.get_all_table_names()
        error_msg = f"Error: {str(e)}\n\nAvailable tables: {available_tables}"
        return error_msg, None, None


def display_chat_history():
    """Display chat history"""
    if "chat_history" in st.session_state and st.session_state.chat_history:
        for i, (question, answer, query, results) in enumerate(reversed(st.session_state.chat_history)):
            with st.chat_message("user"):
                st.write(question)

            with st.chat_message("assistant"):
                st.write(answer)

                if results is not None and not results.empty:
                    with st.expander("📊 Query Results"):
                        st.dataframe(results, use_container_width=True)

            st.divider()


def main():
    st.set_page_config(
        page_title="❇️ APPS SQUARE AI Systems",
        page_icon="🗃️",
        layout="wide"
    )

    # Initialize session state
    if "db_manager" not in st.session_state:
        st.session_state.db_manager = DatabaseManager()
    if "query_generator" not in st.session_state:
        st.session_state.query_generator = QueryGenerator()
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "processed_files" not in st.session_state:
        st.session_state.processed_files = []
    if "input_key" not in st.session_state:
        st.session_state.input_key = 0

    st.title("❇️ APPS SQUARE AI Systems - ADAPTIVE INTELLIGENCE")
    st.markdown("<p style='color: red; font-weight: regular;'>Testing stage - Please verify all answers manually</p>", unsafe_allow_html=True)
    st.markdown(
        "Ask questions about your data in natural language, and I'll generate the answers!")

    # Sidebar for file upload
    with st.sidebar:
        st.subheader("📁 Upload Data Files")

        uploaded_files = st.file_uploader(
            "Upload CSV or Excel files",
            accept_multiple_files=True,
            type=['csv', 'xlsx', 'xls'],
            key="file_uploader",
            help="Supported formats: CSV (.csv), Excel (.xlsx, .xls). Max file size: 50MB"
        )

        # Show supported files info
        if uploaded_files:
            supported_files = [f for f in uploaded_files if f.name.lower().endswith(
                ('.csv', '.xlsx', '.xls'))]
            unsupported_files = [f for f in uploaded_files if not f.name.lower().endswith(
                ('.csv', '.xlsx', '.xls'))]

            if unsupported_files:
                st.warning(
                    f"⚠️ {len(unsupported_files)} unsupported file(s) will be ignored")
                with st.expander("Show unsupported files"):
                    for f in unsupported_files:
                        st.write(f"• {f.name}")

        # Auto-process files when uploaded
        if uploaded_files:
            processed_files = process_uploaded_files(
                uploaded_files, st.session_state.db_manager)
            st.session_state.processed_files = processed_files

        # Show loaded files
        if st.session_state.processed_files:
            st.subheader("📋 Loaded Data")
            for file_info in st.session_state.processed_files:
                st.write(file_info)

        # Show database schema
        if hasattr(st.session_state, 'db_manager') and st.session_state.db_manager.tables_info:
            st.subheader("🗂️ Database Schema")
            for table_name, info in st.session_state.db_manager.tables_info.items():
                with st.expander(f"Table: {table_name}"):
                    st.write(f"**Source:** {info['filename']}")
                    if 'sheet_name' in info:
                        st.write(f"**Sheet:** {info['sheet_name']}")
                    st.write(
                        f"**Dimensions:** {info['shape'][0]} rows × {info['shape'][1]} columns")
                    st.write(f"**SQL Columns:** {', '.join(info['columns'])}")
                    if 'original_columns' in info:
                        st.write(
                            f"**Original Columns:** {', '.join(info['original_columns'])}")

        # Tips section
        st.subheader("💡 Query Examples")
        st.markdown("""
        **Basic queries:**
        - "Show me all the data"
        - "How many rows are in the table?"
        - "What are the column names?"
        
        **Aggregations:**
        - "What's the total sales?"
        - "Show me average price by category"
        - "Count customers by region"
        
        **Filtering:**
        - "Show sales greater than 1000"
        - "Find customers in New York"
        - "Products with price between 10 and 50"
        
        **Sorting:**
        - "Top 10 customers by revenue"
        - "Latest orders by date"
        - "Products sorted by price desc"
        """)

    # Main chat interface
    user_question = st.text_input(
        "Ask a question about your data:",
        placeholder="e.g., What's the total sales by region? Show me the top 10 customers by revenue.",
        key=f"user_input_{st.session_state.input_key}"
    )

    if user_question:
        # Process the question
        answer, sql_query, results = handle_user_question(
            user_question,
            st.session_state.db_manager,
            st.session_state.query_generator
        )

        # Add to chat history
        st.session_state.chat_history.append(
            (user_question, answer, sql_query, results))

        # Clear input
        st.session_state.input_key += 1
        st.rerun()

    # Display chat history
    display_chat_history()


if __name__ == '__main__':
    main()
