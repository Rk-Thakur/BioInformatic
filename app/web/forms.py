from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import DateField, DecimalField, PasswordField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.base import DRUG_CLASSES


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])


class UploadForm(FlaskForm):
    vcf = FileField("VCF file (.vcf or .vcf.gz, GRCh38, one sample)", validators=[FileRequired()])


class MedicationForm(FlaskForm):
    drug_class = SelectField("Drug class", choices=[SelectChoice(c, c) for c in DRUG_CLASSES])
    description = StringField("Medication", validators=[DataRequired(), Length(max=300)])
    start = DateField("Start date", validators=[DataRequired()])


class PatientForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    external_id = StringField(
        "External ID (leave empty to generate)", validators=[Optional(), Length(max=64)]
    )
    sex = SelectField(
        "Sex",
        choices=[
            SelectChoice("", "Not recorded"),
            SelectChoice("F", "Female"),
            SelectChoice("M", "Male"),
        ],
        validators=[Optional()],
    )
    birth_date = DateField("Birth date", validators=[Optional()])
    diabetes_type = SelectField(
        "Diabetes type",
        choices=[
            SelectChoice("type1", "Type 1"),
            SelectChoice("type2", "Type 2"),
            SelectChoice("neonatal", "Neonatal"),
            SelectChoice("unknown", "Unknown"),
        ],
    )
    age_at_diagnosis_years = DecimalField(
        "Age at diagnosis in years (0.25 = 3 months)",
        places=2,
        validators=[Optional(), NumberRange(min=0, max=120)],
    )


class EmptyForm(FlaskForm):
    """Buttons that POST (review, stop medication): CSRF token only."""
