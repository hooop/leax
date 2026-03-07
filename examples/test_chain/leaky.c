#include <stdlib.h>
#include <string.h>


char	*allocate(int size)
{
	char	*buf;

	buf = malloc(size);
	strcpy(buf, "data");
	return (buf);
}

char	*transform(char *input)
{
	char	*output;

	output = malloc(strlen(input) + 10);
	strcpy(output, input);
	strcat(output, "_suffix");
	free(input);
	return (output);
}

void	run(int mode)
{
	char	*original;
	char	*result;

	original = allocate(32);
	if (mode == 1)
	{
		result = transform(original);
		free(result);
		
	}
}

int	main(void)
{
	run(0);
	return (0);
}
