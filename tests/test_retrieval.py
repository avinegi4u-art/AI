from copresenter.models import DocumentRecord
from copresenter.retrieval import answer_question


def test_answer_question_uses_matching_document_text() -> None:
    document = DocumentRecord(
        title="Program overview",
        source_filename="program.txt",
        mime_type="text/plain",
        text=(
            "The program launches in July. The budget is allocated to onboarding, "
            "customer education, and support readiness."
        ),
    )

    answer, citations = answer_question("What is the program budget used for?", [document])

    assert "uploaded materials" in answer
    assert "onboarding" in answer
    assert citations
    assert citations[0].document_id == document.id


def test_answer_question_declines_when_sources_do_not_match() -> None:
    document = DocumentRecord(
        title="Roadmap",
        source_filename="roadmap.txt",
        mime_type="text/plain",
        text="The roadmap covers onboarding improvements and support readiness.",
    )

    answer, citations = answer_question("What is the office lunch menu?", [document])

    assert "do not know" in answer
    assert citations == []
